"""End-to-end: words sent as text → real CleaningPipeline (Presidio + PyMuPDF).

Custom words are removed on top of automatic PII detection, never instead of
it. Needs the NLP models (runs in CI).
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import fitz

from config import Config
from processor.custom_word_detector import CustomWordDetector
from signal_bot import DONE_MESSAGE, SignalBot

SENDER = "42cc8f08-8b0a-4a79-a46d-000000000001"


def _make_bot(tmp_path: Path) -> SignalBot:
    config = Config(
        SIGNAL_PHONE_NUMBER="+972501234567",
        SIGNAL_API_URL="http://localhost:8080",
        TEMP_DIR=tmp_path / "temp",
        MAX_FILE_SIZE_MB=25,
        PROCESSING_TIMEOUT=300,
        ALLOWED_SENDERS=SENDER,
    )
    with patch("signal_bot.SignalClient"):
        bot = SignalBot(config)
    bot._api = MagicMock()
    return bot


def _make_pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), text)
    data = doc.tobytes()
    doc.close()
    return data


def _send_pdf(bot: SignalBot, tmp_path: Path, text: str, caption: str = "") -> tuple[str, list[str]]:
    """Send a PDF; return (text of the cleaned PDF, text replies)."""
    bot._api.send_message.reset_mock()
    pdf = _make_pdf_bytes(text)
    bot.handle_message(sender=SENDER, message=caption,
                       attachments=[{"filename": "t.pdf", "size": len(pdf), "data": pdf}])
    calls = bot._api.send_message.call_args_list
    [file_call] = [c for c in calls if c.kwargs.get("attachments")]
    out = tmp_path / "verify.pdf"
    out.write_bytes(file_call.kwargs["attachments"][0][1])
    doc = fitz.open(str(out))
    cleaned = "".join(page.get_text() for page in doc)
    doc.close()
    return cleaned, [c.kwargs.get("message", "") for c in calls]


def test_words_removed_and_automatic_pii_still_removed(tmp_path: Path):
    bot = _make_bot(tmp_path)
    bot.handle_message(sender=SENDER, message="Zorbo", attachments=None)

    cleaned, replies = _send_pdf(bot, tmp_path, "Project Zorbo. Contact john@example.com today.")

    assert "Zorbo" not in cleaned          # custom word
    assert "john@example.com" not in cleaned  # automatic PII, still on
    assert "Project" in cleaned
    assert DONE_MESSAGE in replies
    assert any(r.startswith("Removed: Zorbo ×1 + ") and "email" in r for r in replies)


def test_caption_words_applied_to_that_file(tmp_path: Path):
    bot = _make_bot(tmp_path)
    cleaned, _ = _send_pdf(bot, tmp_path, "Hello Amit, please review this document.", caption="Amit")
    assert "Amit" not in cleaned
    assert bot._word_store.get_words(SENDER) == ["Amit"]


def test_hebrew_word_removed_from_pdf(tmp_path: Path):
    bot = _make_bot(tmp_path)
    bot.handle_message(sender=SENDER, message="יעקב", attachments=None)
    cleaned, _ = _send_pdf(bot, tmp_path, "שלום יעקב, מה שלומך?")
    assert "יעקב" not in cleaned


def test_without_words_automatic_detection_runs(tmp_path: Path):
    bot = _make_bot(tmp_path)
    with patch.object(bot._pipeline, "process", wraps=bot._pipeline.process) as process:
        cleaned, replies = _send_pdf(bot, tmp_path, "Contact john@example.com")
    detector = process.call_args.kwargs["detector"]
    assert isinstance(detector, CustomWordDetector)
    assert detector._base is bot._pipeline.detector
    assert "john@example.com" not in cleaned
    assert any("No custom words active." in r for r in replies)


def test_words_isolated_per_sender(tmp_path: Path):
    other = "42cc8f08-8b0a-4a79-a46d-000000000002"
    bot = _make_bot(tmp_path)
    bot.handle_message(sender=SENDER, message="Amit", attachments=None)
    assert bot._word_store.has_active_session(SENDER)
    assert not bot._word_store.has_active_session(other)
