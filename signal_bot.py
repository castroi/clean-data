import logging
import secrets
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from pysignalclirestapi import SignalCliRestApi

from config import Config
from processor.pipeline import CleaningPipeline, ProcessingTimeout
from utils.secure_delete import secure_delete, secure_delete_dir

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx"}
RATE_LIMIT_MAX_FILES = 5
RATE_LIMIT_WINDOW_SECONDS = 60

USAGE_MESSAGE = (
    "Welcome to Clean-Data! Send me a PDF or DOCX file and I will remove "
    "all personal data (names, IDs, addresses, emails, phone numbers) and "
    "document metadata.\n\n"
    "Supported formats: PDF, DOCX\n"
    "Max file size: {max_size}MB"
)


class SignalBot:
    """Signal bot that receives documents, cleans PII, and returns them."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()
        self._api = SignalCliRestApi(
            base_url=self._config.SIGNAL_CLI_URL,
            number=self._config.SIGNAL_PHONE_NUMBER,
        )
        self._pipeline = CleaningPipeline(self._config)
        self._temp_dir = Path(self._config.TEMP_DIR)
        self._temp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        # Rate limiting: per-sender timestamps of recent file submissions
        self._rate_limits: dict[str, list[float]] = defaultdict(list)

        # Optional allowlist: comma-separated phone numbers in env var
        allowed = getattr(self._config, "ALLOWED_SENDERS", "")
        self._allowed_senders: set[str] = {
            s.strip() for s in allowed.split(",") if s.strip()
        } if allowed else set()

    def _is_rate_limited(self, sender: str) -> bool:
        """Check if sender has exceeded the rate limit."""
        now = time.time()
        # Prune old timestamps outside the window
        self._rate_limits[sender] = [
            t for t in self._rate_limits[sender]
            if now - t < RATE_LIMIT_WINDOW_SECONDS
        ]
        if len(self._rate_limits[sender]) >= RATE_LIMIT_MAX_FILES:
            return True
        self._rate_limits[sender].append(now)
        return False

    def _send_message(self, recipient: str, message: str) -> None:
        """Send a text message to a Signal recipient."""
        try:
            self._api.send_message(message=message, recipients=[recipient])
        except Exception as e:
            logger.error("Failed to send message: %s", e)

    def _send_attachment(self, recipient: str, file_path: Path, message: str = "") -> None:
        """Send a file attachment to a Signal recipient."""
        try:
            file_bytes = file_path.read_bytes()
            self._api.send_message(
                message=message,
                recipients=[recipient],
                attachments_as_bytes=[file_bytes],
            )
        except Exception as e:
            logger.error("Failed to send attachment: %s", e)

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
        # Allowlist check (if configured)
        if self._allowed_senders and sender not in self._allowed_senders:
            logger.warning("Rejected message from unauthorized sender")
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

        # Sanitize filename: strip path components to prevent traversal
        safe_name = Path(filename).name.replace("/", "").replace("\\", "").replace("\x00", "")
        if not safe_name:
            safe_name = f"attachment{ext}"
        random_id = secrets.token_hex(8)
        input_path = self._temp_dir / f"input_{random_id}_{safe_name}"

        # Verify path is still within temp dir
        if not input_path.resolve().is_relative_to(self._temp_dir.resolve()):
            self._send_message(sender, "Invalid filename.")
            return
        cleaned_path = None

        try:
            # Download attachment from signal-cli REST API
            attachment_id = attachment.get("id")
            if attachment_id:
                content = self._api.get_attachment(attachment_id)
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

            # Process
            cleaned_path = self._pipeline.process(input_path)

            # Send back
            self._send_attachment(
                sender,
                cleaned_path,
                "Done. Original and processed files have been deleted from the server.",
            )

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
            # Always clean up — even on error
            paths_to_delete = [p for p in [input_path, cleaned_path] if p and p.exists()]
            self._pipeline.cleanup(paths_to_delete)

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

    def start(self) -> None:
        """Start polling for incoming Signal messages."""
        self._purge_stale_temp_files()
        logger.info("Clean-Data bot started on %s", self._config.SIGNAL_PHONE_NUMBER)
        logger.info("Polling for messages...")

        while True:
            try:
                messages = self._api.receive()
                for msg in messages:
                    envelope = msg.get("envelope", {})
                    source = envelope.get("source", "")
                    data_message = envelope.get("dataMessage", {})

                    if not source or not data_message:
                        continue

                    text = data_message.get("message", "")
                    attachments = data_message.get("attachments", [])

                    self.handle_message(
                        sender=source,
                        message=text,
                        attachments=attachments if attachments else None,
                    )
            except KeyboardInterrupt:
                logger.info("Bot stopped by user")
                break
            except Exception as e:
                logger.error("Error polling messages: %s", e)

            time.sleep(2)
