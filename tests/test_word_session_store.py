import time
from unittest.mock import patch
from word_session_store import WordSessionStore

SENDER_A = "+972501234567"
SENDER_B = "+972509876543"


def test_add_and_get_words():
    store = WordSessionStore()
    store.add_words(SENDER_A, ["Amit", "Joni"])
    words = store.get_words(SENDER_A)
    assert "Amit" in words
    assert "Joni" in words


def test_accumulate_words():
    """Same sender sending /replace twice — words accumulate."""
    store = WordSessionStore()
    store.add_words(SENDER_A, ["Amit"])
    store.add_words(SENDER_A, ["Joni"])
    words = store.get_words(SENDER_A)
    assert "Amit" in words
    assert "Joni" in words


def test_accumulate_hebrew_words():
    """Test /replace יעקב then /replace יוסף from same sender accumulates both."""
    store = WordSessionStore()
    store.add_words(SENDER_A, ["יעקב"])
    store.add_words(SENDER_A, ["יוסף"])
    words = store.get_words(SENDER_A)
    assert "יעקב" in words
    assert "יוסף" in words


def test_clear_words():
    store = WordSessionStore()
    store.add_words(SENDER_A, ["Amit"])
    store.clear(SENDER_A)
    assert not store.has_active_session(SENDER_A)


def test_expiry():
    store = WordSessionStore()
    store.add_words(SENDER_A, ["Amit"])
    # Simulate 1 hour + 1 second passing
    with patch("word_session_store.time") as mock_time:
        mock_time.time.return_value = time.time() + 3601
        assert not store.has_active_session(SENDER_A)
        assert store.get_words(SENDER_A) == []


def test_per_sender_isolation():
    """Words added by sender A must not appear in sender B's session and vice versa."""
    store = WordSessionStore()
    store.add_words(SENDER_A, ["Amit"])
    store.add_words(SENDER_B, ["Joni"])
    assert store.get_words(SENDER_A) == ["Amit"]
    assert store.get_words(SENDER_B) == ["Joni"]


def test_duplicate_words_not_added():
    store = WordSessionStore()
    store.add_words(SENDER_A, ["Amit", "Amit"])
    store.add_words(SENDER_A, ["Amit"])
    words = store.get_words(SENDER_A)
    assert words.count("Amit") == 1


def test_strips_whitespace():
    store = WordSessionStore()
    store.add_words(SENDER_A, ["  Amit  ", " Joni"])
    words = store.get_words(SENDER_A)
    assert "Amit" in words
    assert "Joni" in words


def test_custom_ttl_from_config():
    """TTL should be configurable via constructor parameter (sourced from WORD_SESSION_TTL_SECONDS)."""
    store = WordSessionStore(ttl_seconds=7200)
    store.add_words(SENDER_A, ["Amit"])
    # Should still be active after 1 hour (TTL is 2 hours)
    with patch("word_session_store.time") as mock_time:
        mock_time.time.return_value = time.time() + 3601
        assert store.has_active_session(SENDER_A)
    # Should be expired after 2 hours + 1 second
    with patch("word_session_store.time") as mock_time:
        mock_time.time.return_value = time.time() + 7201
        assert not store.has_active_session(SENDER_A)
