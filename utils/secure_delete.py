import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def secure_delete(file_path: Path) -> None:
    """Securely overwrite and delete a file."""
    if not file_path.exists():
        logger.warning("File does not exist, skipping secure delete")
        return

    size = file_path.stat().st_size

    if size > 0:
        with open(file_path, "r+b") as f:
            f.write(os.urandom(size))
            f.flush()
            os.fsync(f.fileno())
            f.seek(0)
            f.write(b"\x00" * size)
            f.flush()
            os.fsync(f.fileno())

    file_path.unlink()
    logger.debug("File securely deleted")


def secure_delete_dir(dir_path: Path) -> None:
    """Securely delete all files in a directory, then remove it."""
    if not dir_path.exists():
        logger.warning("Directory does not exist, skipping secure delete")
        return

    for item in dir_path.iterdir():
        if item.is_file():
            secure_delete(item)

    dir_path.rmdir()
    logger.debug("Directory securely deleted")
