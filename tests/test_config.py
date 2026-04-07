import logging

import pytest

from config import parse_allowed_senders


def test_empty_string_returns_empty_set():
    assert parse_allowed_senders("") == set()


def test_whitespace_only_returns_empty_set():
    assert parse_allowed_senders("   ") == set()


def test_single_number_returns_one_entry():
    assert parse_allowed_senders("+972501234567") == {"+972501234567"}


def test_multiple_numbers_comma_separated():
    result = parse_allowed_senders("+972501234567,+17890011234")
    assert result == {"+972501234567", "+17890011234"}


def test_whitespace_around_numbers_stripped():
    assert parse_allowed_senders(" +972 ") == {"+972"}


def test_internal_spaces_removed():
    assert parse_allowed_senders("+17 8900") == {"+178900"}


def test_trailing_and_leading_commas_ignored():
    assert parse_allowed_senders(",+972,,") == {"+972"}


def test_dashes_allowed():
    assert parse_allowed_senders("+1-555-1234") == {"+1-555-1234"}


def test_plus_sign_preserved():
    assert parse_allowed_senders("+972501234567") == {"+972501234567"}


def test_invalid_characters_skipped_with_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="config"):
        result = parse_allowed_senders("+abc123")
    assert result == set()
    assert any("invalid" in record.message.lower() for record in caplog.records)


def test_mixed_valid_and_invalid():
    result = parse_allowed_senders("+972501234567,abc,+1789011")
    assert result == {"+972501234567", "+1789011"}
