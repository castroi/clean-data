# Fix ALLOWED_SENDERS Not Loaded Into Config

**Goal:** Add `ALLOWED_SENDERS` to the Config dataclass so the allowlist is actually loaded from `.env` and parsed into a usable set of phone numbers.

**Architecture:** Add a `parse_allowed_senders()` function in `config.py` that splits, cleans, and validates comma-separated phone numbers. Update `signal_bot.py` to use the Config field directly instead of `getattr`. Keep silent rejection for unauthorized senders.

**Key decisions:**
- Parse in `config.py` with a standalone function (testable independently)
- Remove spaces within numbers (`+17 8900` → `+178900`) since spaces in phone numbers are formatting-only
- Allow `+` and `-` characters, reject letters and other invalid chars
- Log warning and skip invalid entries instead of crashing on startup
- Silent rejection for unauthorized senders (don't confirm bot existence)

---

## Tasks

### Task 1: Add `parse_allowed_senders` and Config field

**Independent:** Yes
**Estimated scope:** Small (1 file)

**Files:**
- Modify: `config.py`

**Steps:**

1. Add `parse_allowed_senders(raw: str) -> set[str]` function that:
   - Returns empty set for empty/whitespace-only input
   - Splits by comma
   - Strips outer whitespace from each entry
   - Removes internal spaces from each entry
   - Validates each entry contains only digits, `+`, `-`
   - Logs warning and skips invalid entries
   - Returns set of cleaned phone numbers

2. Add `ALLOWED_SENDERS` field to Config dataclass:
   - Raw string field loaded from env: `ALLOWED_SENDERS: str = os.getenv("ALLOWED_SENDERS", "")`
   - Since Config is `frozen=True`, cannot use `__post_init__` to add a parsed field
   - The parsing will happen in `signal_bot.py` when reading the field

**Verification:** `pytest tests/test_config.py -v`

---

### Task 2: Update `signal_bot.py` to use Config field

**Independent:** No (depends on Task 1)
**Estimated scope:** Small (1 file)

**Files:**
- Modify: `signal_bot.py` (lines 45-49)

**Steps:**

1. Replace `getattr(self._config, "ALLOWED_SENDERS", "")` with `self._config.ALLOWED_SENDERS`
2. Use `parse_allowed_senders()` to parse the raw string into a set
3. Import `parse_allowed_senders` from `config`

**Verification:** `pytest tests/test_signal_bot.py -v`

---

### Task 3: Write tests for `parse_allowed_senders`

**Independent:** No (depends on Task 1)
**Estimated scope:** Small (1 file)

**Files:**
- Create: `tests/test_config.py`

**Test cases:**

1. Empty string → empty set
2. Single number → set with one entry
3. Multiple numbers comma-separated → correct set
4. Whitespace around numbers stripped: `" +972 "` → `"+972"`
5. Spaces within numbers removed: `"+17 8900"` → `"+178900"`
6. Trailing/leading commas ignored: `",+972,,"` → `{"+972"}`
7. Dashes allowed: `"+1-555-1234"` → `{"+1-555-1234"}`
8. Plus sign preserved: `"+972501234567"` → `{"+972501234567"}`
9. Invalid characters skipped with warning: `"+abc123"` → empty set (logged)
10. Mixed valid and invalid: `"+972501234567,abc,+1789011"` → `{"+972501234567", "+1789011"}`

**Verification:** `pytest tests/test_config.py -v`

---

### Task 4: Update allowlist tests in `test_signal_bot.py`

**Independent:** No (depends on Tasks 1-2)
**Estimated scope:** Small (1 file)

**Files:**
- Modify: `tests/test_signal_bot.py`

**Test cases:**

1. Bot with `ALLOWED_SENDERS` set rejects unauthorized sender silently
2. Bot with `ALLOWED_SENDERS` set allows authorized sender
3. Bot with empty `ALLOWED_SENDERS` allows all senders

**Verification:** `pytest tests/test_signal_bot.py -v`

---

## Dependency Graph

```
Task 1 (config.py) ──► Task 2 (signal_bot.py) ──► Task 4 (test_signal_bot.py)
                   └──► Task 3 (test_config.py)
```

**Sequential:** Task 1 first, then Tasks 2-3 in parallel, then Task 4

---

## Verification Summary

| Task | Verification Command | Expected Output |
|------|---------------------|-----------------|
| 1 | `python -c "from config import parse_allowed_senders; print(parse_allowed_senders('+972,+17 8900'))"` | `{'+972', '+178900'}` |
| 2 | `pytest tests/test_signal_bot.py -v` | All tests pass |
| 3 | `pytest tests/test_config.py -v` | All tests pass |
| 4 | `pytest tests/test_signal_bot.py -v` | All tests pass (including new allowlist tests) |
