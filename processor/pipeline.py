import logging
import signal
from pathlib import Path

from config import Config
from processor.docx_cleaner import DOCXCleaner
from processor.pdf_cleaner import PDFCleaner
from processor.pii_detector import PIIDetector
from utils.secure_delete import secure_delete

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx"}


class ProcessingTimeout(Exception):
    """Raised when document processing exceeds the configured timeout."""


class CleaningPipeline:
    """Orchestrates document cleaning: routes by type, enforces timeout, cleans up."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()
        self._detector = PIIDetector()
        self._pdf_cleaner = PDFCleaner(self._detector)
        self._docx_cleaner = DOCXCleaner(self._detector)

    def process(self, input_path: Path, detector: PIIDetector | None = None) -> Path:
        """Process a document and return the path to the cleaned file.

        Args:
            input_path: Path to the document to process.
            detector: Optional detector override. When provided, a temporary
                cleaner is created using that detector instead of the default
                PIIDetector. Useful for CustomWordDetector sessions.

        Raises ValueError for unsupported file types.
        Raises ProcessingTimeout if processing exceeds the configured limit.
        """
        input_path = Path(input_path)
        ext = input_path.suffix.lower()

        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file type: '{ext}'. Supported: {', '.join(SUPPORTED_EXTENSIONS)}"
            )

        output_path = input_path.parent / f"cleaned_{input_path.name}"

        if detector is not None:
            pdf_cleaner = PDFCleaner(detector)
            docx_cleaner = DOCXCleaner(detector)
        else:
            pdf_cleaner = self._pdf_cleaner
            docx_cleaner = self._docx_cleaner

        def _timeout_handler(signum, frame):
            raise ProcessingTimeout(
                f"Processing exceeded {self._config.PROCESSING_TIMEOUT}s timeout"
            )

        # Set timeout alarm
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(self._config.PROCESSING_TIMEOUT)

        try:
            if ext == ".pdf":
                stats = pdf_cleaner.clean(input_path, output_path)
            else:
                stats = docx_cleaner.clean(input_path, output_path)

            logger.info("Processing complete: %s", stats)
        finally:
            # Cancel alarm and restore handler
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

        return output_path

    def cleanup(self, file_paths: list[Path]) -> None:
        """Securely delete a list of files."""
        for path in file_paths:
            secure_delete(Path(path))
