from dataclasses import dataclass
from pathlib import Path
import logging
import os
import re

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_VALID_PHONE_RE = re.compile(r'^[0-9+\-]+$')


def parse_allowed_senders(raw: str) -> set[str]:
    """Parse a comma-separated string of phone numbers into a set of cleaned numbers.

    Each entry is stripped of surrounding whitespace and internal spaces.
    Entries that contain characters other than digits, '+', or '-' are logged
    as a warning and skipped.  Returns an empty set for empty/whitespace input.
    """
    if not raw or not raw.strip():
        return set()

    result: set[str] = set()
    for entry in raw.split(","):
        stripped = entry.strip()
        if not stripped:
            continue
        # Remove all internal spaces (formatting artefacts like "+17 8900")
        cleaned = stripped.replace(" ", "")
        if not cleaned:
            continue
        if not _VALID_PHONE_RE.match(cleaned):
            logger.warning("Skipping invalid phone number in ALLOWED_SENDERS: %r", stripped)
            continue
        result.add(cleaned)

    return result


@dataclass(frozen=True)
class Config:
    SIGNAL_PHONE_NUMBER: str = os.getenv("SIGNAL_PHONE_NUMBER", "")
    SIGNAL_CLI_URL: str = os.getenv("SIGNAL_CLI_URL", "http://localhost:8080")
    TEMP_DIR: Path = Path(os.getenv("TEMP_DIR", "/tmp/clean-data"))
    MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "25"))
    PROCESSING_TIMEOUT: int = int(os.getenv("PROCESSING_TIMEOUT", "300"))
    ALLOWED_SENDERS: str = os.getenv("ALLOWED_SENDERS", "")
