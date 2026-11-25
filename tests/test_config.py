import logging

import pytest

from config import parse_allowed_senders


@pytest.mark.parametrize("raw", ["", "   ", ",, ,"])
def test_missing_allowlist_stops_startup(raw):
    """Required, like ha-signal-remote's ALLOWLIST_UUIDS."""
    with pytest.raises(ValueError, match="required"):
        parse_allowed_senders(raw)


def test_single_number_returns_one_entry():
    assert parse_allowed_senders("42cc8f08-8b0a-4a79-a46d-000000000001") == {"42cc8f08-8b0a-4a79-a46d-000000000001"}


def test_multiple_numbers_comma_separated():
    result = parse_allowed_senders("42cc8f08-8b0a-4a79-a46d-000000000001,11111111-2222-3333-4444-555555555555")
    assert result == {"42cc8f08-8b0a-4a79-a46d-000000000001", "11111111-2222-3333-4444-555555555555"}


def test_whitespace_around_numbers_stripped():
    assert parse_allowed_senders(" 42cc8f08-8b0a-4a79-a46d-000000000001 ") == {"42cc8f08-8b0a-4a79-a46d-000000000001"}


def test_trailing_and_leading_commas_ignored():
    assert parse_allowed_senders(",42cc8f08-8b0a-4a79-a46d-000000000001,,") == {"42cc8f08-8b0a-4a79-a46d-000000000001"}


def test_uppercase_uuid_lowercased():
    assert parse_allowed_senders("42CC8F08-8B0A-4A79-A46D-000000000001") == {"42cc8f08-8b0a-4a79-a46d-000000000001"}

def test_phone_number_fails_closed(caplog):
    """A phone number must not be skipped (that would open the bot to everyone)."""
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ValueError, match="1 invalid entry"):
            parse_allowed_senders("+972501234567")
    assert "972501234567" not in caplog.text


def test_mixed_valid_and_invalid_fails_closed():
    with pytest.raises(ValueError, match="2 invalid entries") as exc:
        parse_allowed_senders("42cc8f08-8b0a-4a79-a46d-000000000001,abc,+972501234567")
    assert "abc" not in str(exc.value) and "972501234567" not in str(exc.value)
