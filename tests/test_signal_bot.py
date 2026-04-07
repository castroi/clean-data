import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, ANY

import fitz

from config import Config
from signal_bot import SignalBot


@pytest.fixture
def config(tmp_path: Path):
    return Config(
        SIGNAL_PHONE_NUMBER="+972501234567",
        SIGNAL_CLI_URL="http://localhost:8080",
        TEMP_DIR=tmp_path / "temp",
        MAX_FILE_SIZE_MB=25,
        PROCESSING_TIMEOUT=300,
    )


@pytest.fixture
def bot(config: Config):
    with patch("signal_bot.SignalCliRestApi"):
        b = SignalBot(config)
        b._api = MagicMock()
        return b


def _make_pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), text)
    data = doc.tobytes()
    doc.close()
    return data


def test_handles_text_message(bot: SignalBot):
    """Text-only message should return usage instructions."""
    bot.handle_message(sender="+972509999999", message="hello", attachments=None)

    bot._api.send_message.assert_called_once()
    call_kwargs = bot._api.send_message.call_args
    msg = call_kwargs.kwargs.get("message", "") or call_kwargs[1].get("message", "")
    if not msg:
        msg = call_kwargs[0][0] if call_kwargs[0] else ""
    assert "PDF" in msg or "DOCX" in msg


def test_handles_unsupported_file(bot: SignalBot):
    """Unsupported file type should return error message."""
    attachment = {"filename": "data.xlsx", "size": 100, "data": b"dummy"}
    bot.handle_message(sender="+972509999999", message="", attachments=[attachment])

    bot._api.send_message.assert_called_once()
    call_args = bot._api.send_message.call_args
    msg = call_args.kwargs.get("message", "") or call_args[1].get("message", "")
    if not msg:
        msg = call_args[0][0] if call_args[0] else ""
    assert "Unsupported" in msg or "unsupported" in msg


def test_handles_oversized_file(bot: SignalBot):
    """File exceeding max size should be rejected."""
    attachment = {
        "filename": "big.pdf",
        "size": 30 * 1024 * 1024,  # 30MB > 25MB limit
        "data": b"dummy",
    }
    bot.handle_message(sender="+972509999999", message="", attachments=[attachment])

    call_args = bot._api.send_message.call_args
    msg = call_args.kwargs.get("message", "") or call_args[1].get("message", "")
    if not msg:
        msg = call_args[0][0] if call_args[0] else ""
    assert "large" in msg.lower() or "maximum" in msg.lower()


def test_handles_pdf_attachment(bot: SignalBot):
    """Valid PDF should be processed and response sent."""
    pdf_data = _make_pdf_bytes("Contact John Smith at john@example.com")
    attachment = {"filename": "test.pdf", "size": len(pdf_data), "data": pdf_data}

    bot.handle_message(sender="+972509999999", message="", attachments=[attachment])

    # Should have sent at least 2 messages: acknowledgment + result
    assert bot._api.send_message.call_count >= 2


def test_cleanup_on_error(bot: SignalBot):
    """Files should be cleaned up even when processing fails."""
    attachment = {
        "filename": "corrupt.pdf",
        "size": 10,
        "data": b"not a real pdf",
    }

    bot.handle_message(sender="+972509999999", message="", attachments=[attachment])

    # Should have sent acknowledgment + error message
    assert bot._api.send_message.call_count >= 2

    # Temp directory should have no leftover files
    temp_files = list(bot._temp_dir.iterdir())
    assert len(temp_files) == 0


def test_cleanup_after_success(bot: SignalBot):
    """Both original and cleaned files should be deleted after successful processing."""
    pdf_data = _make_pdf_bytes("Contact John Smith")
    attachment = {"filename": "test.pdf", "size": len(pdf_data), "data": pdf_data}

    bot.handle_message(sender="+972509999999", message="", attachments=[attachment])

    # Temp directory should be empty after processing
    temp_files = list(bot._temp_dir.iterdir())
    assert len(temp_files) == 0


# --- Tests for _process_attachment: attachment download via API ---


def test_process_attachment_downloads_by_id(bot: SignalBot):
    """When attachment has 'id', content should be fetched via get_attachment API."""
    pdf_data = _make_pdf_bytes("Contact John Smith at john@example.com")
    bot._api.get_attachment.return_value = pdf_data

    attachment = {"filename": "test.pdf", "size": len(pdf_data), "id": "abc123"}
    bot.handle_message(sender="+972509999999", message="", attachments=[attachment])

    bot._api.get_attachment.assert_called_once_with("abc123")
    # Should have sent acknowledgment + result
    assert bot._api.send_message.call_count >= 2


def test_process_attachment_falls_back_to_data(bot: SignalBot):
    """When attachment has no 'id', should use inline 'data' field."""
    pdf_data = _make_pdf_bytes("Contact John Smith at john@example.com")
    attachment = {"filename": "test.pdf", "size": len(pdf_data), "data": pdf_data}

    bot.handle_message(sender="+972509999999", message="", attachments=[attachment])

    bot._api.get_attachment.assert_not_called()
    assert bot._api.send_message.call_count >= 2


def test_process_attachment_empty_content_from_api(bot: SignalBot):
    """Empty content from get_attachment should send error to user."""
    bot._api.get_attachment.return_value = b""

    attachment = {"filename": "test.pdf", "size": 100, "id": "abc123"}
    bot.handle_message(sender="+972509999999", message="", attachments=[attachment])

    # Find the "empty file" error message
    calls = bot._api.send_message.call_args_list
    messages = []
    for call in calls:
        msg = call.kwargs.get("message", "") or call[1].get("message", "")
        if not msg and call[0]:
            msg = call[0][0]
        messages.append(msg)
    assert any("empty" in m.lower() for m in messages)


def test_process_attachment_api_content_too_large(bot: SignalBot):
    """Content from API exceeding max size should be rejected."""
    bot._api.get_attachment.return_value = b"x" * (26 * 1024 * 1024)  # 26MB

    attachment = {"filename": "test.pdf", "size": 100, "id": "abc123"}
    bot.handle_message(sender="+972509999999", message="", attachments=[attachment])

    calls = bot._api.send_message.call_args_list
    messages = []
    for call in calls:
        msg = call.kwargs.get("message", "") or call[1].get("message", "")
        if not msg and call[0]:
            msg = call[0][0]
        messages.append(msg)
    assert any("large" in m.lower() or "maximum" in m.lower() for m in messages)


# --- Tests for _send_attachment: send file as bytes ---


def test_send_attachment_sends_bytes(bot: SignalBot, tmp_path: Path):
    """_send_attachment should read file and send as attachments_as_bytes."""
    test_file = tmp_path / "cleaned.pdf"
    test_content = b"cleaned pdf content"
    test_file.write_bytes(test_content)

    bot._send_attachment("+972509999999", test_file, "Done.")

    bot._api.send_message.assert_called_once_with(
        message="Done.",
        recipients=["+972509999999"],
        attachments_as_bytes=[test_content],
    )


def test_send_attachment_handles_error(bot: SignalBot, tmp_path: Path):
    """_send_attachment should not raise when API call fails."""
    test_file = tmp_path / "cleaned.pdf"
    test_file.write_bytes(b"content")
    bot._api.send_message.side_effect = Exception("Network error")

    # Should not raise
    bot._send_attachment("+972509999999", test_file, "Done.")


def test_send_attachment_missing_file(bot: SignalBot, tmp_path: Path):
    """_send_attachment should not raise when file doesn't exist."""
    missing_file = tmp_path / "nonexistent.pdf"

    # Should not raise (caught by exception handler)
    bot._send_attachment("+972509999999", missing_file, "Done.")
