"""Zero-retention, allowlist and logging behaviour of SignalBot.

Uses a fake pipeline (no PyMuPDF/Presidio work) so it exercises only the bot's
handling of frames, files and the Signal transport.
"""
import logging
import os
import signal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import shutdown
import signal_bot
from config import Config
from processor.pipeline import ProcessingTimeout, output_path_for
from signal_bot import DONE_MESSAGE, DONE_MESSAGE_NOT_DELETED, SignalBot, collect_attachment_ids

SENDER = "42cc8f08-8b0a-4a79-a46d-000000000001"


@pytest.fixture(autouse=True)
def _clean_shutdown_state():
    """Real SIGTERM handler for these tests; state reset around each."""
    shutdown.reset()
    old = signal.signal(signal.SIGTERM, shutdown.request)
    yield
    signal.signal(signal.SIGTERM, old)
    shutdown.reset()
OTHER = "11111111-2222-3333-4444-555555555555"
PDF = b"%PDF-1.4 fake original with John Smith"


class FakePipeline:
    """Writes the cleaned file like the real pipeline; can fail mid-way."""

    def __init__(self, fail_with: BaseException | None = None, cleaned: bytes = b"%PDF cleaned"):
        self.fail_with = fail_with
        self.cleaned = cleaned
        self.processed: list[Path] = []
        self.events: list[str] | None = None

    def process(self, input_path: Path, detector=None) -> Path:
        if self.events is not None:
            self.events.append("process")
        self.processed.append(input_path)
        out = output_path_for(input_path)
        out.write_bytes(self.cleaned)  # partial output exists before a failure
        if self.fail_with is not None:
            raise self.fail_with
        return out

    def cleanup(self, paths) -> None:
        for p in paths:
            Path(p).unlink()


def _bot(tmp_path: Path, allowed: str = SENDER, pipeline: FakePipeline | None = None, max_send: int = 40_000_000):
    config = Config(
        SIGNAL_PHONE_NUMBER="+15550000001",
        SIGNAL_API_URL="http://localhost:8080",
        TEMP_DIR=tmp_path / "temp",
        ALLOWED_SENDERS=allowed,
        SIGNAL_MAX_SEND_BYTES=max_send,
    )
    with patch("signal_bot.SignalClient"), patch("signal_bot.CleaningPipeline"):
        bot = SignalBot(config)
    bot._api = MagicMock()
    bot._api.get_attachment.return_value = PDF
    bot._pipeline = pipeline or FakePipeline()
    return bot


def _frame(data_message=None, sender=SENDER, **extra) -> dict:
    env = {"sourceUuid": sender, **extra}
    if data_message is not None:
        env["dataMessage"] = data_message
    return {"envelope": env}


def _att(id_="att1", filename="John Smith CV.pdf"):
    return {"id": id_, "filename": filename, "contentType": "application/pdf", "size": len(PDF)}


def _texts(bot) -> list[str]:
    return [c.kwargs.get("message", "") for c in bot._api.send_message.call_args_list]


def _deleted(bot) -> list[str]:
    return [c.args[0] for c in bot._api.delete_attachment.call_args_list]


# --- collect_attachment_ids -------------------------------------------------


def test_collects_ids_from_every_place_signal_cli_downloads():
    a = {"contentType": "image/jpeg"}
    msg = _frame(
        {"attachments": [{**a, "id": "d1"}], "previews": [{"url": "x", "image": {**a, "id": "p1"}}],
         "quote": {"attachments": [{"thumbnail": {**a, "id": "q1"}}]},
         "contacts": [{"avatar": {"attachment": {**a, "id": "c1"}}}],
         "sticker": {"attachment": {**a, "id": "s1"}}},
        editMessage={"dataMessage": {"attachments": [{**a, "id": "e1"}]}},
        syncMessage={"sentMessage": {"attachments": [{**a, "id": "y1"}]}},
        storyMessage={"fileAttachment": {**a, "id": "t1"}},
    )
    assert collect_attachment_ids(msg) == {"d1", "p1", "q1", "c1", "s1", "e1", "y1", "t1"}


def test_collect_ignores_non_attachment_ids_and_junk():
    msg = {"envelope": {"dataMessage": {"groupInfo": {"id": "g"}, "attachments": ["x", None, 3]}}}
    assert collect_attachment_ids(msg) == set()


# --- deletion on every path ---------------------------------------------------


def test_processed_original_deleted_before_processing(tmp_path):
    pipeline = FakePipeline()
    bot = _bot(tmp_path, pipeline=pipeline)
    events: list[str] = []
    pipeline.events = events
    bot._api.delete_attachment.side_effect = lambda i: events.append("delete")

    bot.on_receive(_frame({"message": "", "attachments": [_att()]}))

    assert events == ["delete", "process"]  # original gone before the long step
    assert _deleted(bot) == ["att1"]        # and not deleted twice
    assert _texts(bot)[-1] == DONE_MESSAGE
    assert list((tmp_path / "temp").iterdir()) == []


@pytest.mark.parametrize("extra", [
    {"editMessage": {"dataMessage": {"attachments": [{"id": "x1", "contentType": "image/png"}]}}},
    {"syncMessage": {"sentMessage": {"attachments": [{"id": "x1", "contentType": "image/png"}]}}},
    {"storyMessage": {"fileAttachment": {"id": "x1", "contentType": "image/png"}}},
])
def test_non_data_frames_with_files_are_deleted(tmp_path, extra):
    bot = _bot(tmp_path)
    bot.on_receive(_frame(None, **extra))
    assert _deleted(bot) == ["x1"]
    bot._api.send_message.assert_not_called()


def test_preview_image_deleted_for_text_message(tmp_path):
    bot = _bot(tmp_path)
    bot.on_receive(_frame({"message": "see https://x", "previews": [{"image": {"id": "p1", "contentType": "image/jpeg"}}]}))
    assert _deleted(bot) == ["p1"]


def test_unauthorized_sender_files_deleted_and_id_not_logged(tmp_path, caplog):
    bot = _bot(tmp_path, allowed=OTHER)
    with caplog.at_level(logging.DEBUG):
        bot.on_receive(_frame({"message": "", "attachments": [_att()]}))
    bot._api.send_message.assert_not_called()
    bot._api.get_attachment.assert_not_called()
    assert _deleted(bot) == ["att1"]
    assert SENDER not in caplog.text


def test_message_without_uuid_is_dropped(tmp_path):
    """Identity is the sourceUuid only; a phone number alone never counts."""
    bot = _bot(tmp_path)
    bot.on_receive({"envelope": {"sourceNumber": "+972501234567", "dataMessage": {"message": "hi",
                    "attachments": [_att()]}}})
    bot._api.send_message.assert_not_called()
    assert _deleted(bot) == ["att1"]  # its file is still removed from signal-cli


def test_rejections_logged_as_rate_limited_count(tmp_path, caplog):
    bot = _bot(tmp_path, allowed=OTHER)
    with caplog.at_level(logging.WARNING):
        for _ in range(10):
            bot.on_receive(_frame({"message": "hi"}))
    lines = [r for r in caplog.records if "not in ALLOWED_SENDERS" in r.getMessage()]
    assert len(lines) == 1
    assert SENDER not in caplog.text and "hi" not in caplog.text


def test_unsupported_and_rate_limited_files_deleted(tmp_path):
    bot = _bot(tmp_path)
    atts = [_att(f"a{i}") for i in range(7)] + [_att("exe", "x.exe")]
    bot.on_receive(_frame({"message": "", "attachments": atts}))
    assert sorted(_deleted(bot)) == sorted([f"a{i}" for i in range(7)] + ["exe"])
    assert bot._api.get_attachment.call_count == 5  # file rate limit


def test_delete_failure_reported_honestly(tmp_path, caplog):
    bot = _bot(tmp_path)
    bot._api.delete_attachment.side_effect = RuntimeError("wrapper down")
    with caplog.at_level(logging.ERROR):
        bot.on_receive(_frame({"message": "", "attachments": [_att()]}))
    assert DONE_MESSAGE_NOT_DELETED in _texts(bot)
    assert DONE_MESSAGE not in _texts(bot)
    assert "still in signal-cli" in caplog.text


def test_handler_exception_still_deletes(tmp_path):
    bot = _bot(tmp_path)
    with patch.object(bot, "handle_message", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            bot.on_receive(_frame({"message": "", "attachments": [_att()]}))
    assert _deleted(bot) == ["att1"]


def test_shutdown_mid_processing_cleans_everything(tmp_path):
    """docker stop -> SIGTERM -> SystemExit while cleaning: original gone, no temp files."""
    bot = _bot(tmp_path, pipeline=FakePipeline(fail_with=SystemExit(0)))
    with pytest.raises(SystemExit):
        bot.on_receive(_frame({"message": "", "attachments": [_att()]}))
    assert _deleted(bot) == ["att1"]
    assert list((tmp_path / "temp").iterdir()) == []


@pytest.mark.parametrize("failure", [ProcessingTimeout("t"), RuntimeError("x"), ValueError("v")])
def test_partial_output_removed_on_failure(tmp_path, failure):
    bot = _bot(tmp_path, pipeline=FakePipeline(fail_with=failure))
    bot.on_receive(_frame({"message": "", "attachments": [_att()]}))
    assert list((tmp_path / "temp").iterdir()) == []


# --- temp names, logging, sizes, flood ---------------------------------------


def test_temp_name_has_no_user_filename(tmp_path):
    pipeline = FakePipeline()
    bot = _bot(tmp_path, pipeline=pipeline)
    bot.on_receive(_frame({"message": "", "attachments": [_att(filename="../John Smith ID 123456789.PDF")]}))
    name = pipeline.processed[0].name
    assert name.startswith("input_") and name.endswith(".pdf")
    assert "John" not in name and "123456789" not in name and len(name) < 40


def test_send_errors_log_type_only(tmp_path, caplog):
    bot = _bot(tmp_path)
    bot._api.send_message.side_effect = OSError("/tmp/clean-data/cleaned_input_x_John_Smith.pdf")
    with caplog.at_level(logging.INFO):
        bot.on_receive(_frame({"message": "", "attachments": [_att()]}))
    assert "John_Smith" not in caplog.text
    assert "OSError" in caplog.text


def test_cleaned_file_too_large_to_send_is_reported(tmp_path):
    bot = _bot(tmp_path, pipeline=FakePipeline(cleaned=b"x" * 3000), max_send=3000)
    bot.on_receive(_frame({"message": "", "attachments": [_att()]}))
    assert any("too large to send back" in t for t in _texts(bot))
    assert not any(c.kwargs.get("attachments") for c in bot._api.send_message.call_args_list)
    assert list((tmp_path / "temp").iterdir()) == []


def test_failed_file_send_is_reported(tmp_path):
    bot = _bot(tmp_path)

    def send(message, recipients, attachments=None):
        if attachments:
            raise RuntimeError("413")

    bot._api.send_message.side_effect = send
    bot.on_receive(_frame({"message": "", "attachments": [_att()]}))
    assert "Could not send the cleaned file back. Please try again." in _texts(bot)


def test_text_flood_is_capped(tmp_path):
    bot = _bot(tmp_path)
    for _ in range(50):
        bot.on_receive(_frame({"message": "hi"}))
    assert bot._api.send_message.call_count == signal_bot.RATE_LIMIT_MAX_MESSAGES


@pytest.mark.parametrize("allowed", ["", "+972501234567", f"{SENDER},+972501234567"])
def test_missing_or_invalid_allowlist_stops_startup(tmp_path, allowed):
    with pytest.raises(ValueError):
        _bot(tmp_path, allowed=allowed)


def test_reply_goes_to_sender_uuid(tmp_path):
    bot = _bot(tmp_path)
    bot.on_receive(_frame({"message": "", "attachments": [_att()]}, sourceNumber="+972501234567"))
    recipients = {tuple(c.kwargs["recipients"]) for c in bot._api.send_message.call_args_list}
    assert recipients == {(SENDER,)}


@pytest.mark.parametrize("near_miss", [
    SENDER[:-1] + "2",       # one character different
    SENDER + "0",            # longer
    SENDER[:-1],             # shorter
    " " + SENDER,            # whitespace is not trimmed on the sender side
])
def test_only_exact_uuid_matches(tmp_path, near_miss):
    bot = _bot(tmp_path)
    bot.on_receive(_frame({"message": "hi"}, sender=near_miss))
    bot._api.send_message.assert_not_called()


# --- shutdown: sweeps are never cut short; queued frames are cleaned (N1, N2) ---


def _previews(*ids):
    return [{"url": "x", "image": {"id": i, "contentType": "image/jpeg"}} for i in ids]


def test_sigterm_during_sweep_still_deletes_every_file(tmp_path):
    """A real SIGTERM arriving mid-sweep is postponed until the sweep ends."""
    bot = _bot(tmp_path)
    calls = []

    def delete(attachment_id, **kwargs):
        calls.append((attachment_id, kwargs))
        if len(calls) == 1:
            os.kill(os.getpid(), signal.SIGTERM)  # docker stop, right now

    bot._api.delete_attachment.side_effect = delete
    with pytest.raises(SystemExit):
        bot.on_receive(_frame({"message": "links", "previews": _previews("p1", "p2", "p3")}))
    assert sorted(c[0] for c in calls) == ["p1", "p2", "p3"]
    # after the request, deletes are single short attempts
    for _, kwargs in calls[1:]:
        assert kwargs["attempts"] == 1 and kwargs["timeout"] <= signal_bot.SHUTDOWN_DELETE_TIMEOUT_SECONDS


def test_sigterm_during_processing_then_sweep_completes(tmp_path):
    """SIGTERM while cleaning: original already gone; other files still swept."""
    pipeline = FakePipeline()
    bot = _bot(tmp_path, pipeline=pipeline)

    def process(input_path, detector=None):
        os.kill(os.getpid(), signal.SIGTERM)
        raise AssertionError("not reached")

    pipeline.process = process
    with pytest.raises(SystemExit):
        bot.on_receive(_frame({"message": "", "attachments": [_att()], "previews": _previews("p1")}))
    assert sorted(_deleted(bot)) == ["att1", "p1"]
    assert list((tmp_path / "temp").iterdir()) == []


def test_shutdown_budget_spent_skips_remaining_deletes(tmp_path, monkeypatch):
    bot = _bot(tmp_path)
    monkeypatch.setattr(shutdown, "SHUTDOWN_BUDGET_SECONDS", 0.0)
    with shutdown.deferred():
        shutdown.request()
        assert bot._delete_original("x") is False
        bot._api.delete_attachment.assert_not_called()
        shutdown.reset()  # leave the block without exiting the test


def test_on_shutdown_deletes_files_of_queued_frames(tmp_path, caplog):
    bot = _bot(tmp_path)
    shutdown._requested_at = None
    queued = [
        _frame({"message": "", "attachments": [{"id": "q1", "filename": "John.pdf"}]}),  # no contentType
        _frame({"message": "x", "previews": _previews("q2")}, sender=OTHER),               # unknown sender
        _frame(None, storyMessage={"fileAttachment": {"id": "q3", "contentType": "image/png"}}),
        _frame({"message": "plain text"}),                                                # nothing to delete
    ]
    with caplog.at_level(logging.INFO):
        bot.on_shutdown(queued)
    assert sorted(_deleted(bot)) == ["q1", "q2", "q3"]
    bot._api.send_message.assert_not_called()   # not processed or answered
    bot._api.get_attachment.assert_not_called()
    assert "deleted 3 of 3 attachment(s) from 4 unprocessed message(s)" in caplog.text
    assert "q1" not in caplog.text and "John" not in caplog.text and SENDER not in caplog.text


def test_sigterm_during_on_shutdown_does_not_cut_it_short(tmp_path):
    bot = _bot(tmp_path)
    calls = []

    def delete(attachment_id, **kwargs):
        calls.append(attachment_id)
        if len(calls) == 1:
            os.kill(os.getpid(), signal.SIGTERM)  # a second docker stop / kill -TERM

    bot._api.delete_attachment.side_effect = delete
    with pytest.raises(SystemExit):
        bot.on_shutdown([_frame({"message": "x", "previews": _previews("a", "b", "c")})])
    assert sorted(calls) == ["a", "b", "c"]
