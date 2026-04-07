from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    SIGNAL_PHONE_NUMBER: str = os.getenv("SIGNAL_PHONE_NUMBER", "")
    SIGNAL_CLI_URL: str = os.getenv("SIGNAL_CLI_URL", "http://localhost:8080")
    TEMP_DIR: Path = Path(os.getenv("TEMP_DIR", "/tmp/clean-data"))
    MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "25"))
    PROCESSING_TIMEOUT: int = int(os.getenv("PROCESSING_TIMEOUT", "300"))
