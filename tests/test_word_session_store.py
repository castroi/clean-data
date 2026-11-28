import time
from unittest.mock import patch

import pytest

from word_session_store import WordSessionStore

SENDER_A = "+972501234567"
SENDER_B = "+972509876543"


def _later(seconds: float):
    """Patch the store's clock to ``seconds`` from now."""
    mock = patch("word_session_store.time")
    m = mock.start()
    m.time.return_value = time.time() + seconds
    return mock


def test_add_and_get_words_in_order():
    store = WordSessionStore()
    added, existing = store.add_words(SENDER_A, ["Joni", "Amit"])
    assert added == ["Joni", "Amit"]
    assert existing == []
    assert store.get_words(SENDER_A) == ["Joni", "Amit"]


def test_accumulate_words():
    store = WordSessionStore()
    store.add_words(SENDER_A, ["Amit"])
    store.add_words(SENDER_A, ["יוסף"])
    assert store.get_words(SENDER_A) == ["Amit", "יוסף"]


def test_duplicates_case_insensitive_first_spelling_kept():
    store = WordSessionStore()
    store.add_words(SENDER_A, ["Amit", "amit"])
    added, existing = store.add_words(SENDER_A, ["AMIT", "Joni"])
    assert added == ["Joni"]
    assert existing == ["AMIT"]
    assert store.get_words(SENDER_A) == ["Amit", "Joni"]


def test_strips_whitespace_and_ignores_empty():
    store = WordSessionStore()
    store.add_words(SENDER_A, ["  Amit  ", " Joni", "", "   "])
    assert store.get_words(SENDER_A) == ["Amit", "Joni"]
    assert store.add_words(SENDER_B, ["  "]) == ([], [])
    assert not store.has_active_session(SENDER_B)


def test_remove_words():
    store = WordSessionStore()
    store.add_words(SENDER_A, ["Amit", "Dana Cohen"])
    removed, missing = store.remove_words(SENDER_A, ["dana cohen", "Ruti"])
    assert removed == ["Dana Cohen"]
    assert missing == ["Ruti"]
    assert store.get_words(SENDER_A) == ["Amit"]


def test_removing_last_word_ends_session():
    store = WordSessionStore()
    store.add_words(SENDER_A, ["Amit"])
    store.remove_words(SENDER_A, ["Amit"])
    assert not store.has_active_session(SENDER_A)


def test_remove_without_session():
    store = WordSessionStore()
    assert store.remove_words(SENDER_A, ["Amit"]) == ([], ["Amit"])


def test_clear_words():
    store = WordSessionStore()
    store.add_words(SENDER_A, ["Amit"])
    store.clear(SENDER_A)
    assert not store.has_active_session(SENDER_A)


def test_default_ttl_is_two_hours():
    store = WordSessionStore()
    store.add_words(SENDER_A, ["Amit"])
    p = _later(7199)
    try:
        assert store.has_active_session(SENDER_A)
    finally:
        p.stop()
    p = _later(7201)
    try:
        assert not store.has_active_session(SENDER_A)
        assert store.get_words(SENDER_A) == []
    finally:
        p.stop()


def test_touch_extends_a_live_session():
    """Each message/file restarts the clock, so a long batch keeps its words."""
    store = WordSessionStore(ttl_seconds=100)
    store.add_words(SENDER_A, ["Amit"])
    p = _later(90)
    try:
        store.touch(SENDER_A)
    finally:
        p.stop()
    p = _later(150)  # 60 s after the touch, 150 s after adding
    try:
        assert store.get_words(SENDER_A) == ["Amit"]
    finally:
        p.stop()


def test_touch_never_revives_an_expired_session():
    store = WordSessionStore(ttl_seconds=100)
    store.add_words(SENDER_A, ["Amit"])
    p = _later(101)
    try:
        store.touch(SENDER_A)
        assert not store.has_active_session(SENDER_A)
    finally:
        p.stop()


def test_touch_without_session_is_a_no_op():
    store = WordSessionStore()
    store.touch(SENDER_A)
    assert not store.has_active_session(SENDER_A)


def test_per_sender_isolation():
    store = WordSessionStore()
    store.add_words(SENDER_A, ["Amit"])
    store.add_words(SENDER_B, ["Joni"])
    assert store.get_words(SENDER_A) == ["Amit"]
    assert store.get_words(SENDER_B) == ["Joni"]


def test_word_limit_adds_nothing():
    store = WordSessionStore(max_words=3)
    store.add_words(SENDER_A, ["a1", "a2"])
    with pytest.raises(ValueError, match="Word limit"):
        store.add_words(SENDER_A, ["a3", "a4"])
    assert store.get_words(SENDER_A) == ["a1", "a2"]
    # Words already on the list don't count against the limit
    assert store.add_words(SENDER_A, ["A1", "a3"]) == (["a3"], ["A1"])


def test_word_limit_on_first_message_leaves_no_empty_session():
    store = WordSessionStore(max_words=1, max_sessions=1)
    with pytest.raises(ValueError):
        store.add_words(SENDER_A, ["a1", "a2"])
    store.add_words(SENDER_B, ["b1"])  # the failed attempt did not take the only slot


def test_session_limit():
    store = WordSessionStore(max_sessions=1)
    store.add_words(SENDER_A, ["Amit"])
    with pytest.raises(ValueError, match="Too many active sessions"):
        store.add_words(SENDER_B, ["Joni"])


def test_long_word_truncated():
    store = WordSessionStore(max_word_length=5)
    store.add_words(SENDER_A, ["abcdefgh"])
    assert store.get_words(SENDER_A) == ["abcde"]


def test_words_normalized_for_dedupe_and_removal():
    import unicodedata
    store = WordSessionStore()
    store.add_words(SENDER_A, ["Dana  Cohen", unicodedata.normalize("NFD", "José")])
    assert store.add_words(SENDER_A, ["dana cohen", "JOSÉ"]) == ([], ["dana cohen", "JOSÉ"])
    assert store.remove_words(SENDER_A, ["Dana Cohen"]) == (["Dana Cohen"], [])
    assert store.get_words(SENDER_A) == ["José"]


def test_hard_cap_even_when_active():
    store = WordSessionStore(ttl_seconds=100, max_age_seconds=250)
    store.add_words(SENDER_A, ["Amit"])
    for offset in (90, 180, 240):
        p = _later(offset)
        try:
            store.touch(SENDER_A)
            assert store.has_active_session(SENDER_A)
        finally:
            p.stop()
    p = _later(251)
    try:
        assert not store.has_active_session(SENDER_A)
    finally:
        p.stop()


def test_adding_words_does_not_extend_past_hard_cap():
    store = WordSessionStore(ttl_seconds=100, max_age_seconds=150)
    store.add_words(SENDER_A, ["Amit"])
    p = _later(90)  # still within the 100 s idle timeout
    try:
        store.add_words(SENDER_A, ["Ruti"])
    finally:
        p.stop()
    p = _later(151)
    try:
        assert store.get_words(SENDER_A) == []
    finally:
        p.stop()
