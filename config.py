from dataclasses import dataclass
from pathlib import Path
import logging
import os
import re

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_VALID_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


def parse_allowed_senders(raw: str) -> set[str]:
    """Parse a comma-separated string of Signal UUIDs into a set.

    Each entry is stripped of surrounding whitespace and lowercased.
    Entries that are not valid UUIDs are logged as a warning and skipped.
    Returns an empty set for empty/whitespace input.
    """
    if not raw or not raw.strip():
        return set()

    result: set[str] = set()
    for entry in raw.split(","):
        stripped = entry.strip()
        if not stripped:
            continue
        cleaned = stripped.lower()
        if not _VALID_UUID_RE.match(cleaned):
            logger.warning("Skipping invalid UUID in ALLOWED_SENDERS: %r", stripped)
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
    WORD_SESSION_TTL_SECONDS: int = int(os.getenv("WORD_SESSION_TTL_SECONDS", "3600"))
    MAX_WORDS_PER_SENDER: int = int(os.getenv("MAX_WORDS_PER_SENDER", "15"))
    MAX_WORD_LENGTH: int = int(os.getenv("MAX_WORD_LENGTH", "100"))
    MAX_WORD_SESSIONS: int = int(os.getenv("MAX_WORD_SESSIONS", "100"))
