import logging

from config import parse_allowed_senders


def test_empty_string_returns_empty_set():
    assert parse_allowed_senders("") == set()


def test_whitespace_only_returns_empty_set():
    assert parse_allowed_senders("   ") == set()


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


def test_invalid_characters_skipped_with_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="config"):
        result = parse_allowed_senders("+abc123")
    assert result == set()
    assert any("invalid" in record.message.lower() for record in caplog.records)


def test_mixed_valid_and_invalid():
    result = parse_allowed_senders("42cc8f08-8b0a-4a79-a46d-000000000001,abc,11111111-2222-3333-4444-555555555555")
    assert result == {"42cc8f08-8b0a-4a79-a46d-000000000001", "11111111-2222-3333-4444-555555555555"}
