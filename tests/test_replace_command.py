"""Integration tests for /replace and /end command flow.

Tests the full chain: command parsing → WordSessionStore → CustomWordDetector
→ Pipeline.process() → document cleaned with custom words removed.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

import fitz

from config import Config
from signal_bot import SignalBot
from word_session_store import WordSessionStore
from processor.custom_word_detector import CustomWordDetector
from processor.pipeline import CleaningPipeline


SENDER = "42cc8f08-8b0a-4a79-a46d-000000000001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path) -> Config:
    return Config(
        SIGNAL_PHONE_NUMBER="+972501234567",
        SIGNAL_CLI_URL="http://localhost:8080",
        TEMP_DIR=tmp_path / "temp",
        MAX_FILE_SIZE_MB=25,
        PROCESSING_TIMEOUT=300,
        ALLOWED_SENDERS="",
    )


def _make_bot(config: Config) -> SignalBot:
    with patch("signal_bot.SignalCliRestApi"):
        bot = SignalBot(config)
        bot._api = MagicMock()
        return bot


def _extract_messages(bot: SignalBot) -> list[str]:
    """Return all text messages sent by the bot during the test."""
    return [
        call.kwargs.get("message", "") or (call.args[0] if call.args else "")
        for call in bot._api.send_message.call_args_list
    ]


def _make_pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), text)
    data = doc.tobytes()
    doc.close()
    return data


def _read_pdf_text(path: Path) -> str:
    doc = fitz.open(str(path))
    text = "".join(page.get_text() for page in doc)
    doc.close()
    return text


# ---------------------------------------------------------------------------
# /replace command parsing — English words
# ---------------------------------------------------------------------------


def test_replace_command_stores_english_words(tmp_path: Path):
    """/replace Amit, Joni stores words and sends confirmation."""
    bot = _make_bot(_make_config(tmp_path))
    bot.handle_message(sender=SENDER, message="/replace Amit, Joni", attachments=None)

    messages = _extract_messages(bot)
    assert any("2 total active" in m for m in messages)
    assert bot._word_store.has_active_session(SENDER)
    words = bot._word_store.get_words(SENDER)
    assert "Amit" in words
    assert "Joni" in words


def test_replace_command_stores_hebrew_words(tmp_path: Path):
    """/replace יעקב, יוסף stores Hebrew words and sends confirmation."""
    bot = _make_bot(_make_config(tmp_path))
    bot.handle_message(sender=SENDER, message="/replace יעקב, יוסף", attachments=None)

    messages = _extract_messages(bot)
    assert any("2 total active" in m for m in messages)
    words = bot._word_store.get_words(SENDER)
    assert "יעקב" in words
    assert "יוסף" in words


def test_replace_command_accumulates_words(tmp_path: Path):
    """Multiple /replace messages accumulate words rather than overwriting."""
    bot = _make_bot(_make_config(tmp_path))
    bot.handle_message(sender=SENDER, message="/replace Amit", attachments=None)
    bot.handle_message(sender=SENDER, message="/replace roni 43", attachments=None)

    words = bot._word_store.get_words(SENDER)
    assert "Amit" in words
    assert "roni 43" in words


def test_replace_command_accumulates_hebrew_and_english(tmp_path: Path):
    """Hebrew and English words accumulate across multiple /replace messages."""
    bot = _make_bot(_make_config(tmp_path))
    bot.handle_message(sender=SENDER, message="/replace יעקב", attachments=None)
    bot.handle_message(sender=SENDER, message="/replace Amit", attachments=None)

    words = bot._word_store.get_words(SENDER)
    assert "יעקב" in words
    assert "Amit" in words


# ---------------------------------------------------------------------------
# /end command
# ---------------------------------------------------------------------------


def test_end_command_clears_words(tmp_path: Path):
    """/end clears all custom words and confirms."""
    bot = _make_bot(_make_config(tmp_path))
    bot.handle_message(sender=SENDER, message="/replace Amit, Joni", attachments=None)
    assert bot._word_store.has_active_session(SENDER)

    bot._api.send_message.reset_mock()
    bot.handle_message(sender=SENDER, message="/end", attachments=None)

    assert not bot._word_store.has_active_session(SENDER)
    messages = _extract_messages(bot)
    assert any("cleared" in m.lower() or "automatic" in m.lower() for m in messages)


def test_end_command_without_active_session(tmp_path: Path):
    """/end when no session is active should not raise."""
    bot = _make_bot(_make_config(tmp_path))
    bot.handle_message(sender=SENDER, message="/end", attachments=None)

    # Should send a confirmation without error
    assert bot._api.send_message.call_count == 1


# ---------------------------------------------------------------------------
# Unknown command
# ---------------------------------------------------------------------------


def test_unknown_command_returns_error(tmp_path: Path):
    """An unrecognized /command should return a helpful error message."""
    bot = _make_bot(_make_config(tmp_path))
    bot.handle_message(sender=SENDER, message="/unknown", attachments=None)

    messages = _extract_messages(bot)
    assert any("unknown" in m.lower() or "/replace" in m.lower() for m in messages)


# ---------------------------------------------------------------------------
# Pipeline uses CustomWordDetector when session is active
# ---------------------------------------------------------------------------


def test_pipeline_uses_custom_detector_when_session_active(tmp_path: Path):
    """Documents processed after /replace use CustomWordDetector to remove custom words."""
    bot = _make_bot(_make_config(tmp_path))

    # Set custom words
    bot.handle_message(sender=SENDER, message="/replace Amit", attachments=None)
    bot._api.send_message.reset_mock()

    # Send a PDF containing the custom word
    pdf_data = _make_pdf_bytes("Hello Amit, please review this document.")
    attachment = {"filename": "test.pdf", "size": len(pdf_data), "data": pdf_data}
    bot.handle_message(sender=SENDER, message="", attachments=[attachment])

    # Verify the bot attempted to send an attachment (cleaned file)
    calls_with_attachment = [
        c for c in bot._api.send_message.call_args_list
        if c.kwargs.get("attachments_as_bytes")
    ]
    assert len(calls_with_attachment) == 1

    # Check the cleaned content has "Amit" removed
    cleaned_bytes = calls_with_attachment[0].kwargs["attachments_as_bytes"][0]
    cleaned_path = tmp_path / "verify_cleaned.pdf"
    cleaned_path.write_bytes(cleaned_bytes)
    cleaned_text = _read_pdf_text(cleaned_path)
    assert "Amit" not in cleaned_text


def test_pipeline_uses_default_detector_when_no_session(tmp_path: Path):
    """Documents processed without /replace session use default PIIDetector."""
    bot = _make_bot(_make_config(tmp_path))
    assert not bot._word_store.has_active_session(SENDER)

    # Verify that no custom detector is created when no session
    with patch.object(bot._pipeline, "process", wraps=bot._pipeline.process) as mock_process:
        pdf_data = _make_pdf_bytes("Contact John Smith at john@example.com")
        attachment = {"filename": "test.pdf", "size": len(pdf_data), "data": pdf_data}
        bot.handle_message(sender=SENDER, message="", attachments=[attachment])

        mock_process.assert_called_once()
        call_kwargs = mock_process.call_args.kwargs
        # detector should be None when no session
        assert call_kwargs.get("detector") is None


def test_pipeline_uses_custom_detector_passed_correctly(tmp_path: Path):
    """When session is active, pipeline.process() receives a CustomWordDetector instance."""
    bot = _make_bot(_make_config(tmp_path))
    bot.handle_message(sender=SENDER, message="/replace Amit", attachments=None)
    bot._api.send_message.reset_mock()

    with patch.object(bot._pipeline, "process", wraps=bot._pipeline.process) as mock_process:
        pdf_data = _make_pdf_bytes("Hello world")
        attachment = {"filename": "test.pdf", "size": len(pdf_data), "data": pdf_data}
        bot.handle_message(sender=SENDER, message="", attachments=[attachment])

        mock_process.assert_called_once()
        call_kwargs = mock_process.call_args.kwargs
        assert isinstance(call_kwargs.get("detector"), CustomWordDetector)


# ---------------------------------------------------------------------------
# Hebrew words end-to-end through pipeline
# ---------------------------------------------------------------------------


def test_hebrew_replace_removes_words_from_pdf(tmp_path: Path):
    """/replace יעקב removes the Hebrew name from a processed PDF."""
    bot = _make_bot(_make_config(tmp_path))
    bot.handle_message(sender=SENDER, message="/replace יעקב", attachments=None)
    bot._api.send_message.reset_mock()

    pdf_data = _make_pdf_bytes("שלום יעקב, מה שלומך?")
    attachment = {"filename": "test.pdf", "size": len(pdf_data), "data": pdf_data}
    bot.handle_message(sender=SENDER, message="", attachments=[attachment])

    calls_with_attachment = [
        c for c in bot._api.send_message.call_args_list
        if c.kwargs.get("attachments_as_bytes")
    ]
    assert len(calls_with_attachment) == 1

    cleaned_bytes = calls_with_attachment[0].kwargs["attachments_as_bytes"][0]
    cleaned_path = tmp_path / "verify_heb.pdf"
    cleaned_path.write_bytes(cleaned_bytes)
    cleaned_text = _read_pdf_text(cleaned_path)
    assert "יעקב" not in cleaned_text


# ---------------------------------------------------------------------------
# Per-sender isolation
# ---------------------------------------------------------------------------


def test_replace_session_isolated_per_sender(tmp_path: Path):
    """Words set by sender A do not affect sender B's processing."""
    sender_a = "42cc8f08-8b0a-4a79-a46d-000000000001"
    sender_b = "42cc8f08-8b0a-4a79-a46d-000000000002"

    bot = _make_bot(_make_config(tmp_path))
    bot.handle_message(sender=sender_a, message="/replace Amit", attachments=None)

    assert bot._word_store.has_active_session(sender_a)
    assert not bot._word_store.has_active_session(sender_b)
