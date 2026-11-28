import logging
import re
import secrets
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import shutdown
from config import Config, load_signal_token, parse_allowed_senders
from processor.custom_word_detector import CustomWordDetector
from processor.pipeline import CleaningPipeline, ProcessingTimeout, output_path_for
from signal_client import SignalClient
from utils.secure_delete import secure_delete, secure_delete_dir
from word_session_store import WordSessionStore

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx"}
RATE_LIMIT_MAX_FILES = 5
RATE_LIMIT_WINDOW_SECONDS = 60
# Any message (text or file) per sender per window; beyond it, messages are
# ignored silently (attachments are still deleted from signal-cli).
RATE_LIMIT_MAX_MESSAGES = 20

DONE_MESSAGE = (
    "Done. Your original has been deleted from the server, and this cleaned "
    "copy is deleted right after sending."
)
DONE_MESSAGE_NOT_DELETED = (
    "Done. Note: your original could not be deleted from the Signal relay "
    "automatically. The operator has been alerted."
)

HELP_MESSAGE = (
    "Clean-Data removes personal info (names, IDs, phones, emails, addresses) "
    "and metadata from PDF and DOCX files (max {max_size}MB).\n\n"
    "Now: {status}\n\n"
    "• Send a file → cleaned file back\n"
    "• Send names/words → also removed from your next files, e.g. Amit Levi, דני כהן\n"
    "• -word → take one word off your list\n"
    "• list / רשימה → show your list · 0 / נקה → clear it\n\n"
    "Hebrew names are not detected automatically. Add them as words."
)

# Whole-message control words (case-insensitive, trailing !.? ignored).
# Anything else sent as text is a list of words to remove.
CLEAR_WORDS = {"0", "clear", "נקה", "ביטול", "/end"}
LIST_WORDS = {"list", "רשימה"}
HELP_WORDS = {"help", "עזרה", "?", "/help", "/start", "start", "hi", "hello", "hey", "שלום", "היי", "הי"}
# Chat replies that must never become words to remove.
ACK_WORDS = {"thanks", "thank you", "thx", "ok", "okay", "cool", "תודה", "תודה רבה", "אוקיי", "אוקי", "סבבה", "מעולה"}
LEGACY_REPLACE = "/replace"
REMOVE_PREFIXES = ("-", "–", "—")
# A caption item longer than this many words gets a reply instead of being saved quietly.
CAPTION_QUIET_MAX_WORDS = 3

# Labels for automatic PII types in the summary (counts only, never values).
AUTO_LABELS = {
    "PERSON": "name",
    "EMAIL_ADDRESS": "email",
    "PHONE_NUMBER": "phone",
    "LOCATION": "location",
    "IL_ID_NUMBER": "ID",
    "CREDIT_CARD": "card",
    "IBAN_CODE": "IBAN",
    "IP_ADDRESS": "IP",
    "URL": "link",
}

_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
_HAS_ALNUM_RE = re.compile(r"\w")
_SPLIT_RE = re.compile(r"[,\n]")


def _normalize_control(text: str) -> str:
    """Lowercased, inner spaces collapsed, trailing !. dropped ("Thanks!" → "thanks")."""
    text = " ".join(text.lower().split())
    return text if text == "?" else text.rstrip("!.?").strip()


def _join(words: list[str]) -> str:
    return ", ".join(words)


def _ttl_text(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{max(1, round(seconds / 60))} min"


class SignalBot:
    """Signal bot that receives documents, cleans PII, and returns them."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()
        self._api = SignalClient(
            base_url=self._config.SIGNAL_API_URL,
            number=self._config.SIGNAL_PHONE_NUMBER,
            token=load_signal_token(self._config),
            heartbeat_path=Path(self._config.HEARTBEAT_FILE) if self._config.HEARTBEAT_FILE else None,
        )
        self._pipeline = CleaningPipeline(self._config)
        self._temp_dir = Path(self._config.TEMP_DIR)
        self._temp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        # Rate limiting: per-sender timestamps of recent file submissions / messages
        self._rate_limits: dict[str, list[float]] = defaultdict(list)
        self._message_rate_limits: dict[str, list[float]] = defaultdict(list)

        # Attachment ids of the frame being handled that were already deleted
        # from signal-cli (so the final sweep doesn't delete them twice).
        self._deleted_ids: set[str] = set()

        # Required allowlist of Signal UUIDs (ACIs). Missing/invalid stops startup.
        self._allowed_senders: set[str] = parse_allowed_senders(self._config.ALLOWED_SENDERS)
        # Rejections are logged at most once per window, as a count, never an id.
        self._rejected_since_log = 0
        self._last_reject_log = 0.0

        # Per-sender custom word lists (words sent as plain text)
        self._word_store = WordSessionStore(
            ttl_seconds=self._config.WORD_SESSION_TTL_SECONDS,
            max_words=self._config.MAX_WORDS_PER_SENDER,
            max_word_length=self._config.MAX_WORD_LENGTH,
            max_sessions=self._config.MAX_WORD_SESSIONS,
            max_age_seconds=self._config.MAX_WORD_SESSION_AGE_SECONDS,
        )

    @staticmethod
    def _hit(limits: dict[str, list[float]], sender: str, max_hits: int) -> bool:
        """Record a hit for sender; return True if over max_hits in the window."""
        now = time.time()
        # Prune old timestamps outside the window
        limits[sender] = [t for t in limits[sender] if now - t < RATE_LIMIT_WINDOW_SECONDS]
        if len(limits[sender]) >= max_hits:
            return True
        limits[sender].append(now)
        return False

    def _log_rejection(self) -> None:
        self._rejected_since_log += 1
        now = time.time()
        if now - self._last_reject_log >= RATE_LIMIT_WINDOW_SECONDS:
            logger.warning("Dropped %d message(s) from senders not in ALLOWED_SENDERS", self._rejected_since_log)
            self._rejected_since_log = 0
            self._last_reject_log = now

    def _is_rate_limited(self, sender: str) -> bool:
        """Check if sender has exceeded the file rate limit."""
        return self._hit(self._rate_limits, sender, RATE_LIMIT_MAX_FILES)

    def _send_message(self, recipient: str, message: str) -> None:
        """Send a text message to a Signal recipient."""
        try:
            self._api.send_message(message=message, recipients=[recipient])
        except Exception as e:
            logger.error("Failed to send message (type: %s)", type(e).__name__)
            logger.debug("Send error detail: %s", e)

    def _send_attachment(self, recipient: str, file_path: Path, message: str = "") -> bool:
        """Send a file attachment to a Signal recipient. Returns True on success."""
        try:
            file_bytes = file_path.read_bytes()
            # Generic name: the original filename may itself contain PII.
            self._api.send_message(
                message=message,
                recipients=[recipient],
                attachments=[(f"cleaned{file_path.suffix.lower()}", file_bytes)],
            )
            return True
        except Exception as e:
            logger.error("Failed to send attachment (type: %s)", type(e).__name__)
            logger.debug("Send error detail: %s", e)
            return False

    def _fits_send_limit(self, size: int) -> bool:
        """Whether a file of ``size`` bytes fits the wrapper's send body cap as base64."""
        encoded = 4 * ((size + 2) // 3)
        return encoded + 1024 <= self._config.SIGNAL_MAX_SEND_BYTES

    def _delete_original(self, attachment_id: str) -> bool:
        """Delete one received file from signal-cli. Returns True if it is gone.

        During shutdown the transport makes single short attempts within the
        shutdown budget (see SignalClient.delete_attachment).
        """
        try:
            self._api.delete_attachment(attachment_id)
        except TimeoutError:
            return False  # shutdown budget spent; counted by the caller
        except Exception as e:
            logger.error("Failed to delete attachment from signal-cli (type: %s)", type(e).__name__)
            return False
        self._deleted_ids.add(attachment_id)
        return True

    def _get_file_extension(self, filename: str) -> str:
        """Extract and normalize file extension."""
        return Path(filename).suffix.lower()

    def handle_message(self, sender: str, message: str, attachments: list[dict] | None = None) -> None:
        """Handle an incoming Signal message.

        Args:
            sender: The phone number of the message sender.
            message: The text content of the message.
            attachments: List of attachment dicts with 'filename', 'id', 'size' keys.
        """
        # Allowlist first. Unknown senders are dropped silently: no reply, no id
        # or message logged, only a rate-limited count.
        if sender.lower() not in self._allowed_senders:
            self._log_rejection()
            return

        # Flood guard for any message (text or file); ignored silently.
        if self._hit(self._message_rate_limits, sender, RATE_LIMIT_MAX_MESSAGES):
            logger.warning("Message rate limit exceeded; message ignored")
            return

        # Any message from the sender keeps their word list alive.
        self._word_store.touch(sender)

        text = (message or "").strip()
        if text:
            # A caption on a file is handled the same way, but quietly: the
            # file's summary already shows the words that were applied.
            self._handle_text(sender, text, with_file=bool(attachments))
        elif not attachments:
            self._send_message(sender, self._help_text(sender))

        if not attachments:
            return

        for attachment in attachments:
            if self._is_rate_limited(sender):
                self._send_message(sender, "Rate limit exceeded. Please wait before sending more files.")
                return
            self._process_attachment(sender, attachment)

    # --- text: control words, words to add, -words to remove ---------------

    def _help_text(self, sender: str) -> str:
        words = self._word_store.get_words(sender)
        if words:
            status = f"automatic removal + {len(words)} custom word(s) ({_join(words)})."
        else:
            status = "automatic removal only (no custom words)."
        return HELP_MESSAGE.format(max_size=self._config.MAX_FILE_SIZE_MB, status=status)

    def _list_text(self, sender: str) -> str:
        words = self._word_store.get_words(sender)
        if not words:
            return "No custom words. Automatic PII removal only."
        return f"Your words (copy and send to reuse later):\n{_join(words)}"

    def _handle_text(self, sender: str, text: str, with_file: bool = False) -> None:
        """Handle the text of a message (or a file's caption).

        Whole-message control words first; then chat filler and unusable text
        (which never become words); otherwise a comma/newline separated list:
        "-word" items leave the list, the rest join it.
        """
        control = _normalize_control(text)
        legacy = False

        if control in CLEAR_WORDS:
            self._word_store.clear(sender)
            self._send_message(sender, "Custom words cleared. Automatic PII removal only.")
            return
        if control in LIST_WORDS:
            self._send_message(sender, self._list_text(sender))
            return
        if control in HELP_WORDS or control in ACK_WORDS:
            if not with_file:
                self._send_message(
                    sender,
                    self._help_text(sender) if control in HELP_WORDS else "Not added as a word.",
                )
            return
        if control.startswith(LEGACY_REPLACE):
            # Old habit: accept the words, say the command is no longer needed.
            text = text.strip()[len(LEGACY_REPLACE):]
            legacy = True
        elif text.startswith("/") or _URL_RE.search(text) or not _HAS_ALNUM_RE.search(text) or len(text) < 2:
            # Unknown command, a link, emoji/punctuation only, or one character.
            if not with_file:
                self._send_message(sender, self._help_text(sender))
            return

        to_add, to_remove = [], []
        for item in _SPLIT_RE.split(text):
            item = item.strip()
            if item.startswith(REMOVE_PREFIXES):
                item = item.lstrip("".join(REMOVE_PREFIXES)).strip()
                if item:
                    to_remove.append(item)
            elif item:
                to_add.append(item)

        if not to_add and not to_remove:
            if not with_file:
                self._send_message(sender, self._help_text(sender))
            return

        lines: list[str] = []
        if to_remove:
            removed, missing = self._word_store.remove_words(sender, to_remove)
            if removed:
                lines.append(f"Removed: {_join(removed)}.")
            if missing:
                lines.append(f"Not on your list: {_join(missing)}.")

        if to_add:
            try:
                added, existing = self._word_store.add_words(sender, to_add)
            except ValueError as e:
                self._send_message(
                    sender,
                    f"{e} Nothing was added. Take some off with -word, or send 0 to clear the list.",
                )
                return
            if added:
                lines.append(f"Will remove: {_join(added)} (+ automatic PII).")
            if existing:
                lines.append(f"Already on your list: {_join(existing)}.")

        # A caption of short names stays quiet (the summary after the file shows
        # them). One that reads like a sentence ("contract for Dana Cohen") was
        # still saved as a phrase, so say so: it likely won't match anything.
        sentence_like = any(len(item.split()) > CAPTION_QUIET_MAX_WORDS for item in to_add)
        if with_file and not to_remove and not legacy and not sentence_like:
            return
        if with_file and sentence_like:
            lines.append("Saved from the file's caption. Separate names with commas if this isn't one name.")

        words = self._word_store.get_words(sender)
        if words:
            if to_remove:
                lines.append(f"Your list: {_join(words)}")
            lines.append(f"Kept for {_ttl_text(self._word_store.ttl_seconds)} after your last message.")
            lines.append("Wrong one? Send -word. Clear all: 0")
        else:
            lines.append("Your list is empty. Automatic PII removal only.")
        if legacy:
            lines.append("No command needed next time: just send the words.")
        self._send_message(sender, "\n".join(lines))

    @staticmethod
    def _summary_text(detector: CustomWordDetector) -> str:
        """What was removed from one file: custom words by name, automatic PII
        as counts per type only (never the values found)."""
        auto_total = sum(detector.auto_hits.values())
        if auto_total:
            by_type = ", ".join(
                f"{n} {AUTO_LABELS.get(t, t.lower())}" for t, n in detector.auto_hits.most_common()
            )
            auto = f"{auto_total} automatic ({by_type})"
        else:
            auto = "no automatic PII found"

        if not detector.custom_hits:
            return f"Removed: {auto}.\nNo custom words active."
        custom = ", ".join(f"{w} ×{n}" for w, n in detector.custom_hits.items())
        lines = [f"Removed: {custom} + {auto}."]
        missing = [w for w, n in detector.custom_hits.items() if n == 0]
        if missing:
            lines.append(f"Not found: {_join(missing)}. Try another spelling?")
        return "\n".join(lines)

    def _process_attachment(self, sender: str, attachment: dict) -> None:
        """Process a single file attachment."""
        filename = attachment.get("filename", "unknown")
        ext = self._get_file_extension(filename)

        if ext not in SUPPORTED_EXTENSIONS:
            self._send_message(sender, f"Unsupported file format: '{ext}'. Supported: PDF, DOCX")
            return

        # Check file size
        size_bytes = attachment.get("size", 0)
        max_bytes = self._config.MAX_FILE_SIZE_MB * 1024 * 1024
        if size_bytes > max_bytes:
            self._send_message(
                sender,
                f"File too large ({size_bytes // (1024*1024)}MB). Maximum: {self._config.MAX_FILE_SIZE_MB}MB",
            )
            return

        # Acknowledge
        self._send_message(sender, "Processing your document...")

        # Temp name from a random token + the validated extension only: the
        # user's filename (which may itself contain PII) never touches disk or
        # logs, and cannot cause path traversal or over-long names.
        input_path = self._temp_dir / f"input_{secrets.token_hex(8)}{ext}"

        # Verify path is still within temp dir
        if not input_path.resolve().is_relative_to(self._temp_dir.resolve()):
            self._send_message(sender, "Invalid filename.")
            return
        cleaned_path = None
        original_deleted = True

        try:
            # Download attachment from signal-cli REST API
            attachment_id = attachment.get("id")
            if attachment_id:
                content = self._api.get_attachment(attachment_id, max_bytes)
                # Remove the original from signal-cli as soon as we hold it,
                # before the (long) processing step.
                original_deleted = self._delete_original(attachment_id)
            else:
                content = attachment.get("data", b"")
                if isinstance(content, str):
                    content = content.encode()
            if len(content) > max_bytes:
                self._send_message(
                    sender,
                    f"File too large. Maximum: {self._config.MAX_FILE_SIZE_MB}MB",
                )
                return
            if not content:
                self._send_message(sender, "Received empty file. Please try again.")
                return
            input_path.write_bytes(content)

            # Automatic PII is always removed; the sender's words are added on
            # top. A fresh detector per file counts what this file contained.
            detector = CustomWordDetector(self._word_store.get_words(sender), base=self._pipeline.detector)

            # Process
            cleaned_path = self._pipeline.process(input_path, detector=detector)

            # Send back (only if it fits the wrapper's body cap)
            size = cleaned_path.stat().st_size
            if not self._fits_send_limit(size):
                self._send_message(
                    sender,
                    f"The cleaned file is too large to send back over Signal "
                    f"({size // (1024 * 1024)}MB). Please split the document and try again.",
                )
                return
            if not original_deleted:
                logger.error("Original attachment still in signal-cli after retries")
            sent = self._send_attachment(
                sender,
                cleaned_path,
                DONE_MESSAGE if original_deleted else DONE_MESSAGE_NOT_DELETED,
            )
            if not sent:
                self._send_message(sender, "Could not send the cleaned file back. Please try again.")
            else:
                # A separate message, never the file's caption: the caption
                # travels with the file when it is forwarded, and this text
                # names the custom words.
                self._send_message(sender, self._summary_text(detector))

        except ProcessingTimeout:
            logger.error("Processing timed out for attachment")
            self._send_message(sender, "Processing timed out. Please try a smaller file.")

        except ValueError as e:
            logger.error("Validation error processing attachment")
            logger.debug("Validation detail: %s", e)
            self._send_message(sender, "Invalid file. Supported formats: PDF, DOCX.")

        except Exception as e:
            logger.error("Processing failed (type: %s)", type(e).__name__)
            logger.debug("Processing error detail: %s", e)
            self._send_message(sender, "An error occurred while processing your document.")

        finally:
            # Always clean up — even on error. Include the pipeline's output
            # path: a timeout/exception can leave a partial cleaned file that
            # process() never returned.
            candidates = {input_path, output_path_for(input_path)}
            if cleaned_path is not None:
                candidates.add(cleaned_path)
            self._pipeline.cleanup([p for p in candidates if p.exists()])

    def _purge_stale_temp_files(self) -> None:
        """Securely delete any leftover temp files from previous runs."""
        if not self._temp_dir.exists():
            return
        stale_files = list(self._temp_dir.iterdir())
        if stale_files:
            logger.warning("Purging %d stale temp files from previous run", len(stale_files))
            for item in stale_files:
                if item.is_file():
                    secure_delete(item)

    def on_receive(self, msg: dict) -> None:
        """Handle one frame from the receive stream.

        Every attachment id anywhere in the frame (data, edit, sync and story
        messages, previews, quote thumbnails, contact avatars, ...) is deleted
        from signal-cli when the frame is done, whatever happened to it.
        """
        ids = frame_attachment_ids(msg)
        self._deleted_ids = set()
        try:
            envelope = msg.get("envelope") or {}
            data_message = envelope.get("dataMessage") or {}
            if not isinstance(data_message, dict) or not data_message:
                return

            # Identity is pinned on the ACI (sourceUuid), never the phone number
            # (which can be hidden or change). No UUID -> drop.
            sender = envelope.get("sourceUuid") or ""
            if not isinstance(sender, str) or not sender:
                return

            attachments = [a for a in data_message.get("attachments") or [] if isinstance(a, dict)]
            self.handle_message(
                sender=sender,
                message=data_message.get("message") or "",
                attachments=attachments if attachments else None,
            )
        finally:
            # A SIGTERM arriving here is postponed until every id was tried.
            with shutdown.deferred():
                left_behind = sum(
                    not self._delete_original(attachment_id) for attachment_id in ids - self._deleted_ids
                )
                if left_behind:
                    logger.error("%d attachment(s) of this message left in signal-cli", left_behind)

    def on_shutdown(self, frames: list[dict]) -> None:
        """Delete the attachments of frames not fully handled at shutdown.

        ``frames`` are the queued ones plus the one in flight. signal-cli does
        not redeliver them, so without this their files would stay in its
        store for good. They are not processed or answered. Ids of the
        in-flight frame that were already deleted are skipped.
        """
        with shutdown.deferred():
            ids: set[str] = set()
            for msg in frames:
                ids |= frame_attachment_ids(msg)
            ids -= self._deleted_ids
            if not ids:
                return
            deleted = sum(self._delete_original(attachment_id) for attachment_id in ids)
            # Logged inside the block: on a real SIGTERM the block ends in
            # SystemExit, so a line after it would never be written.
            log = logger.warning if deleted == len(ids) else logger.error
            log(
                "Shutdown: deleted %d of %d attachment(s) from %d unhandled message(s); %d left in signal-cli",
                deleted, len(ids), len(frames), len(ids) - deleted,
            )

    def start(self) -> None:
        """Listen for incoming Signal messages on the wrapper's receive stream."""
        self._purge_stale_temp_files()
        logger.info("Clean-Data bot started on %s", self._config.SIGNAL_PHONE_NUMBER)
        try:
            self._api.receive_forever(self.on_receive, on_shutdown=self.on_shutdown)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Bot stopped")


def frame_attachment_ids(msg: dict) -> set[str]:
    """All attachment ids in a receive frame: every attachment-shaped object,
    plus anything listed in dataMessage.attachments even if fields are missing.
    """
    ids = collect_attachment_ids(msg)
    envelope = msg.get("envelope") if isinstance(msg, dict) else None
    data_message = envelope.get("dataMessage") if isinstance(envelope, dict) else None
    if isinstance(data_message, dict):
        for a in data_message.get("attachments") or []:
            if isinstance(a, dict) and isinstance(a.get("id"), str) and a["id"]:
                ids.add(a["id"])
    return ids


def collect_attachment_ids(obj: object, _depth: int = 0) -> set[str]:
    """Return the id of every attachment-shaped object anywhere in ``obj``.

    signal-cli downloads all of them into its store: dataMessage/editMessage/
    syncMessage/storyMessage attachments, link-preview images, quote
    thumbnails, contact avatars and sticker files. An attachment is a dict with
    a string ``id`` and a ``contentType`` key.
    """
    found: set[str] = set()
    if _depth > 64:
        return found
    if isinstance(obj, dict):
        if isinstance(obj.get("id"), str) and obj["id"] and "contentType" in obj:
            found.add(obj["id"])
        for value in obj.values():
            found |= collect_attachment_ids(value, _depth + 1)
    elif isinstance(obj, list):
        for value in obj:
            found |= collect_attachment_ids(value, _depth + 1)
    return found
