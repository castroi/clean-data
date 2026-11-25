import logging
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

# Per-delete limits once shutdown has started (all must fit the shutdown budget).
SHUTDOWN_DELETE_TIMEOUT_SECONDS = 5.0

DONE_MESSAGE = (
    "Done. Your original has been deleted from the server, and this cleaned "
    "copy is deleted right after sending."
)
DONE_MESSAGE_NOT_DELETED = (
    "Done. Note: your original could not be deleted from the Signal relay "
    "automatically. The operator has been alerted."
)

USAGE_MESSAGE = (
    "Welcome to Clean-Data! Send me a PDF or DOCX file and I will remove "
    "all personal data (names, IDs, addresses, emails, phone numbers) and "
    "document metadata.\n\n"
    "Supported formats: PDF, DOCX\n"
    "Max file size: {max_size}MB\n\n"
    "Commands:\n"
    "/replace word1, word2, ... — remove specific words instead of automatic PII detection\n"
    "/end — clear custom words and restore automatic PII detection"
)


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

        # Per-sender custom word sessions for /replace command
        self._word_store = WordSessionStore(
            ttl_seconds=self._config.WORD_SESSION_TTL_SECONDS,
            max_words=self._config.MAX_WORDS_PER_SENDER,
            max_word_length=self._config.MAX_WORD_LENGTH,
            max_sessions=self._config.MAX_WORD_SESSIONS,
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

        Once shutdown has started, one short attempt each, and nothing after
        the shutdown budget is spent (the container is about to be killed).
        """
        try:
            if shutdown.requested():
                left = shutdown.remaining()
                if left <= 0:
                    return False
                self._api.delete_attachment(
                    attachment_id, timeout=min(SHUTDOWN_DELETE_TIMEOUT_SECONDS, left), attempts=1
                )
            else:
                self._api.delete_attachment(attachment_id)
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

        # Command handling — must happen before attachment check
        if message and message.strip().startswith("/"):
            self._handle_command(sender, message.strip())
            # If the message has no attachments, we're done
            if not attachments:
                return

        if not attachments:
            self._send_message(
                sender,
                USAGE_MESSAGE.format(max_size=self._config.MAX_FILE_SIZE_MB),
            )
            return

        for attachment in attachments:
            if self._is_rate_limited(sender):
                self._send_message(sender, "Rate limit exceeded. Please wait before sending more files.")
                return
            self._process_attachment(sender, attachment)

    def _handle_command(self, sender: str, message: str) -> None:
        """Handle a /command message from a sender."""
        if message.lower().startswith("/replace "):
            raw_words = message[len("/replace "):]
            words = [w.strip() for w in raw_words.split(",") if w.strip()]
            if not words:
                self._send_message(sender, "Usage: /replace word1, word2, word3")
                return
            try:
                all_words = self._word_store.add_words(sender, words)
            except ValueError as e:
                self._send_message(sender, str(e))
                return
            self._send_message(
                sender,
                f"{len(words)} word(s) added ({len(all_words)} total active).\n"
                f"These words will be removed from documents sent in the next hour.\n"
                f"Send /end to clear.",
            )
        elif message.lower().strip() == "/end":
            self._word_store.clear(sender)
            self._send_message(sender, "Custom words cleared. Back to automatic PII detection.")
        else:
            self._send_message(sender, "Unknown command. Available: /replace, /end")

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

            # Choose detector: custom words override default PII detection
            detector = None
            if self._word_store.has_active_session(sender):
                detector = CustomWordDetector(self._word_store.get_words(sender))

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
                for attachment_id in ids - self._deleted_ids:
                    self._delete_original(attachment_id)

    def on_shutdown(self, frames: list[dict]) -> None:
        """Delete the attachments of frames still queued at shutdown.

        signal-cli does not redeliver them, so without this their files would
        stay in its store for good. They are not processed or answered.
        """
        ids: set[str] = set()
        for msg in frames:
            ids |= frame_attachment_ids(msg)
        if not ids:
            return
        deleted = 0
        with shutdown.deferred():
            for attachment_id in ids:
                if self._delete_original(attachment_id):
                    deleted += 1
        logger.warning(
            "Shutdown: deleted %d of %d attachment(s) from %d unprocessed message(s)",
            deleted, len(ids), len(frames),
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
