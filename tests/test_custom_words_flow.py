"""Plain-text custom-word flow of SignalBot: no commands needed.

Text = words to remove (comma or newline separated), "-word" takes one off,
whole-message control words (0/נקה, list/רשימה, help/עזרה), chat filler is
never stored, captions add words quietly, automatic PII always runs and every
file is followed by a separate summary message.

Uses a fake pipeline (no PyMuPDF/Presidio), so it runs without NLP models.
"""
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from docx import Document

import signal_bot
from config import Config
from processor.custom_word_detector import CustomWordDetector
from processor.docx_cleaner import DOCXCleaner
from processor.pipeline import output_path_for
from signal_bot import DONE_MESSAGE, SignalBot

SENDER = "42cc8f08-8b0a-4a79-a46d-000000000001"
OTHER = "42cc8f08-8b0a-4a79-a46d-000000000002"
PDF = b"%PDF-1.4 fake"


class FakeBase:
    """Automatic detector stand-in: finds every 'a@b.com' as an email."""

    def detect_entities(self, text):
        return [
            {"type": "EMAIL_ADDRESS", "text": m.group(), "start": m.start(), "end": m.end(), "score": 1.0}
            for m in re.finditer(re.escape("a@b.com"), text)
        ]

    def detect_and_remove(self, text):
        return " ".join(text.replace("a@b.com", "").split())


class FakePipeline:
    """Runs the bot's detector over ``doc_text`` so its counts are real."""

    def __init__(self, doc_text: str = ""):
        self.detector = FakeBase()
        self.doc_text = doc_text
        self.used: list[CustomWordDetector] = []

    def process(self, input_path: Path, detector=None) -> Path:
        self.used.append(detector)
        detector.detect_entities(self.doc_text)
        out = output_path_for(input_path)
        out.write_bytes(b"%PDF cleaned")
        return out

    def cleanup(self, paths) -> None:
        for p in paths:
            Path(p).unlink()


def _bot(tmp_path: Path, doc_text: str = "", **cfg) -> SignalBot:
    config = Config(
        SIGNAL_PHONE_NUMBER="+15550000001",
        SIGNAL_API_URL="http://localhost:8080",
        TEMP_DIR=tmp_path / "temp",
        ALLOWED_SENDERS=f"{SENDER},{OTHER}",
        **cfg,
    )
    with patch("signal_bot.SignalClient"), patch("signal_bot.CleaningPipeline"):
        bot = SignalBot(config)
    bot._api = MagicMock()
    bot._api.get_attachment.return_value = PDF
    bot._pipeline = FakePipeline(doc_text)
    return bot


def _att(id_="att1"):
    return {"id": id_, "filename": "x.pdf", "contentType": "application/pdf", "size": len(PDF)}


def _send(bot, text, files=None, sender=SENDER) -> list[str]:
    """Send one message; return the texts the bot replied with."""
    bot._api.send_message.reset_mock()
    bot.handle_message(sender=sender, message=text, attachments=files)
    return [c.kwargs.get("message", "") for c in bot._api.send_message.call_args_list]


def _words(bot, sender=SENDER):
    return bot._word_store.get_words(sender)


# --- text becomes words -----------------------------------------------------


def test_text_adds_words_and_confirms(tmp_path):
    bot = _bot(tmp_path)
    [reply] = _send(bot, "Amit Levi, דני כהן")
    assert _words(bot) == ["Amit Levi", "דני כהן"]
    assert "Will remove: Amit Levi, דני כהן (+ automatic PII)." in reply
    assert "Kept for 2h after your last message." in reply
    assert "-word" in reply and "0" in reply


def test_newlines_also_separate_words(tmp_path):
    bot = _bot(tmp_path)
    _send(bot, "Amit Levi\nRuti\n\n  054-1234567 ")
    assert _words(bot) == ["Amit Levi", "Ruti", "054-1234567"]


def test_space_does_not_split_a_phrase(tmp_path):
    bot = _bot(tmp_path)
    _send(bot, "roni 43")
    assert _words(bot) == ["roni 43"]


def test_words_accumulate_and_duplicates_reported(tmp_path):
    bot = _bot(tmp_path)
    _send(bot, "Amit")
    [reply] = _send(bot, "amit, Ruti")
    assert _words(bot) == ["Amit", "Ruti"]
    assert "Will remove: Ruti" in reply
    assert "Already on your list: amit." in reply


def test_word_limit_message(tmp_path):
    bot = _bot(tmp_path, MAX_WORDS_PER_SENDER=2)
    [reply] = _send(bot, "a1, a2, a3")
    assert "Word limit reached (2). Nothing was added." in reply
    assert _words(bot) == []


# --- -word removes one word ----------------------------------------------------


def test_minus_removes_one_word(tmp_path):
    bot = _bot(tmp_path)
    _send(bot, "Amit Levi, דני כהן, Ruti")
    [reply] = _send(bot, "-דני כהן")
    assert _words(bot) == ["Amit Levi", "Ruti"]
    assert "Removed: דני כהן." in reply
    assert "Your list: Amit Levi, Ruti" in reply


def test_minus_variants_and_unknown_word(tmp_path):
    bot = _bot(tmp_path)
    _send(bot, "Amit, Ruti")
    [reply] = _send(bot, "– amit, -Joni")
    assert _words(bot) == ["Ruti"]
    assert "Removed: Amit." in reply
    assert "Not on your list: Joni." in reply


def test_add_and_remove_in_one_message(tmp_path):
    bot = _bot(tmp_path)
    _send(bot, "Amit")
    _send(bot, "-Amit, Ruti")
    assert _words(bot) == ["Ruti"]


def test_removing_last_word_says_auto_only(tmp_path):
    bot = _bot(tmp_path)
    _send(bot, "Amit")
    [reply] = _send(bot, "-Amit")
    assert "Your list is empty. Automatic PII removal only." in reply


# --- control words ----------------------------------------------------------------


@pytest.mark.parametrize("word", ["0", "clear", "CLEAR", "נקה", "ביטול", "/end"])
def test_clear_words(tmp_path, word):
    bot = _bot(tmp_path)
    _send(bot, "Amit")
    [reply] = _send(bot, word)
    assert _words(bot) == []
    assert "cleared" in reply.lower()


@pytest.mark.parametrize("word", ["list", "List", "רשימה"])
def test_list_is_a_copyable_line(tmp_path, word):
    bot = _bot(tmp_path)
    _send(bot, "Amit Levi, Ruti")
    [reply] = _send(bot, word)
    assert reply == "Your words (copy and send to reuse later):\nAmit Levi, Ruti"
    assert _words(bot) == ["Amit Levi", "Ruti"]  # not added as words


def test_list_copy_pasted_back_restores_words(tmp_path):
    """The whole point of the list reply: paste it back, no retyping."""
    bot = _bot(tmp_path)
    _send(bot, "Amit Levi, Ruti")
    copied = _send(bot, "list")[0].split("\n", 1)[1]
    _send(bot, "0")
    _send(bot, copied)
    assert _words(bot) == ["Amit Levi", "Ruti"]


def test_empty_list(tmp_path):
    bot = _bot(tmp_path)
    assert _send(bot, "list") == ["No custom words. Automatic PII removal only."]


@pytest.mark.parametrize("word", ["help", "HELP", "עזרה", "?", "/help", "/start", "hi", "Hello!", "שלום"])
def test_help_shows_status_and_is_not_stored(tmp_path, word):
    bot = _bot(tmp_path)
    [reply] = _send(bot, word)
    assert "Clean-Data" in reply
    assert "Now: automatic removal only (no custom words)." in reply
    assert "Hebrew names are not detected automatically" in reply
    assert _words(bot) == []


def test_help_shows_active_words(tmp_path):
    bot = _bot(tmp_path)
    _send(bot, "Amit, Ruti")
    [reply] = _send(bot, "help")
    assert "Now: automatic removal + 2 custom word(s) (Amit, Ruti)." in reply


# --- text that must never become words ------------------------------------------


@pytest.mark.parametrize("text", ["thanks", "Thanks!", "תודה", "ok", "אוקיי", "תודה רבה"])
def test_chat_filler_not_added(tmp_path, text):
    bot = _bot(tmp_path)
    assert _send(bot, text) == ["Not added as a word."]
    assert _words(bot) == []


@pytest.mark.parametrize("text", ["👍", "!!", "x", "see https://example.com", "www.site.co.il", "/unknown"])
def test_unusable_text_gets_help(tmp_path, text):
    bot = _bot(tmp_path)
    [reply] = _send(bot, text)
    assert "Clean-Data" in reply
    assert _words(bot) == []


def test_empty_message_gets_help(tmp_path):
    bot = _bot(tmp_path)
    [reply] = _send(bot, "")
    assert "Clean-Data" in reply


def test_legacy_replace_still_works_with_a_hint(tmp_path):
    bot = _bot(tmp_path)
    [reply] = _send(bot, "/replace Amit, Joni")
    assert _words(bot) == ["Amit", "Joni"]
    assert "No command needed next time" in reply


# --- files: automatic PII always, words on top, summary after ------------------


def test_file_without_words_uses_automatic_detection(tmp_path):
    bot = _bot(tmp_path, doc_text="mail a@b.com and a@b.com")
    replies = _send(bot, "", [_att()])
    [detector] = bot._pipeline.used
    assert detector._base is bot._pipeline.detector  # automatic detection on
    assert replies[-2] == DONE_MESSAGE
    assert replies[-1] == "Cleaned with: automatic PII removal only (no custom words)."


def test_file_with_words_keeps_automatic_detection(tmp_path):
    bot = _bot(tmp_path, doc_text="Amit wrote a@b.com, amit again")
    _send(bot, "Amit, Ruti")
    replies = _send(bot, "", [_att()])
    [detector] = bot._pipeline.used
    assert detector._base is bot._pipeline.detector
    assert replies[-1] == "Cleaned with: automatic PII removal + your words (Amit, Ruti)."


def test_caption_adds_words_quietly_before_cleaning(tmp_path):
    bot = _bot(tmp_path, doc_text="Ruti signed")
    replies = _send(bot, "Ruti", [_att()])
    assert _words(bot) == ["Ruti"]
    assert not any("Will remove" in r for r in replies)  # no extra confirmation
    assert replies[-1] == "Cleaned with: automatic PII removal + your words (Ruti)."


def test_caption_words_stay_for_the_next_file(tmp_path):
    bot = _bot(tmp_path, doc_text="Ruti")
    _send(bot, "Ruti", [_att("a1")])
    replies = _send(bot, "", [_att("a2")])
    assert replies[-1] == "Cleaned with: automatic PII removal + your words (Ruti)."


def test_one_caption_applies_to_every_file_in_the_message(tmp_path):
    bot = _bot(tmp_path, doc_text="Ruti")
    replies = _send(bot, "Ruti", [_att("a1"), _att("a2")])
    assert replies.count("Cleaned with: automatic PII removal + your words (Ruti).") == 2


def test_sentence_like_caption_gets_a_reply(tmp_path):
    """"contract for Dana Cohen" is saved as one phrase: tell the user."""
    bot = _bot(tmp_path, doc_text="Dana Cohen")
    replies = _send(bot, "contract for Dana Cohen", [_att()])
    assert _words(bot) == ["contract for Dana Cohen"]
    [note] = [r for r in replies if r.startswith("Will remove:")]
    assert "Separate names with commas" in note
    assert replies[-1] == "Cleaned with: automatic PII removal + your words (contract for Dana Cohen)."


def test_short_names_caption_stays_quiet(tmp_path):
    bot = _bot(tmp_path)
    replies = _send(bot, "Dana Cohen, Ruti", [_att()])
    assert not any(r.startswith("Will remove:") for r in replies)


def test_caption_filler_ignored_silently(tmp_path):
    bot = _bot(tmp_path)
    replies = _send(bot, "thanks", [_att()])
    assert _words(bot) == []
    assert "Not added as a word." not in replies
    assert DONE_MESSAGE in replies


def test_summary_is_separate_from_file_caption(tmp_path):
    """The file's caption travels when forwarded: it must not name any word."""
    bot = _bot(tmp_path, doc_text="Amit")
    _send(bot, "Amit")
    _send(bot, "", [_att()])
    [file_call] = [c for c in bot._api.send_message.call_args_list if c.kwargs.get("attachments")]
    assert file_call.kwargs["message"] == DONE_MESSAGE
    assert "Amit" not in file_call.kwargs["message"]


def test_no_summary_when_file_send_fails(tmp_path):
    bot = _bot(tmp_path, doc_text="Amit")
    bot._api.send_message.side_effect = lambda **kw: (_ for _ in ()).throw(RuntimeError()) if kw.get("attachments") else None
    replies = _send(bot, "", [_att()])
    assert not any(r.startswith("Cleaned with:") for r in replies)


# --- session lifetime -------------------------------------------------------------


def test_any_message_keeps_the_list_alive(tmp_path):
    bot = _bot(tmp_path, WORD_SESSION_TTL_SECONDS=100)
    _send(bot, "Amit")
    with patch("word_session_store.time") as t:
        import time as real_time
        start = real_time.time()
        t.time.return_value = start + 90
        _send(bot, "list")                 # resets the 100 s clock
        t.time.return_value = start + 150  # 60 s after the last message
        assert _words(bot) == ["Amit"]
        t.time.return_value = start + 191
        assert _words(bot) == []


def test_ttl_shown_in_minutes_when_not_whole_hours(tmp_path):
    bot = _bot(tmp_path, WORD_SESSION_TTL_SECONDS=1800)
    [reply] = _send(bot, "Amit")
    assert "Kept for 30 min after your last message." in reply


def test_words_are_per_sender(tmp_path):
    bot = _bot(tmp_path)
    _send(bot, "Amit")
    assert _words(bot, OTHER) == []
    assert "No custom words" in _send(bot, "list", sender=OTHER)[0]


def test_flood_guard_still_applies_to_text(tmp_path):
    bot = _bot(tmp_path)
    for i in range(signal_bot.RATE_LIMIT_MAX_MESSAGES + 5):
        bot.handle_message(sender=SENDER, message=f"w{i}", attachments=None)
    assert len(_words(bot)) == signal_bot.RATE_LIMIT_MAX_MESSAGES


def test_hard_cap_clears_an_active_list(tmp_path):
    """Activity can't keep a list (PII) in memory past the hard cap."""
    bot = _bot(tmp_path, WORD_SESSION_TTL_SECONDS=100, MAX_WORD_SESSION_AGE_SECONDS=250)
    _send(bot, "Amit")
    with patch("word_session_store.time") as t:
        import time as real_time
        start = real_time.time()
        for offset in (90, 180):
            t.time.return_value = start + offset
            _send(bot, "list")             # keeps it alive...
        t.time.return_value = start + 251  # ...but not past 250 s after it started
        assert _words(bot) == []


# --- DOCX end to end (python-docx only, no NLP) ------------------------------------


def test_docx_custom_words_and_automatic_pii_removed(tmp_path):
    src, out = tmp_path / "in.docx", tmp_path / "out.docx"
    doc = Document()
    doc.add_paragraph("Signed by Dana Cohen and לדני, mail a@b.com.")
    doc.add_paragraph("Jordan Danger stays.")
    doc.save(str(src))

    detector = CustomWordDetector(["Dana Cohen", "דני", "Dan"], base=FakeBase())
    DOCXCleaner(detector).clean(src, out)

    text = "\n".join(p.text for p in Document(str(out)).paragraphs)
    assert "Dana Cohen" not in text
    assert "דני" not in text
    assert "a@b.com" not in text
    assert "Jordan Danger stays." in text
