# /replace Command — Custom Word Removal via Signal Bot

**Goal:** Allow users to send `/replace word1, word2, ...` via Signal to specify custom words to remove from subsequent documents, with `/end` to clear. Words accumulate across multiple `/replace` messages and expire after 1 hour.

**Architecture:** A `CustomWordDetector` subclass of `PIIDetector` overrides entity detection to do case-insensitive plain text matching instead of NLP. A `WordSessionStore` manages per-sender word lists with 1-hour TTL. When a sender has active custom words, the pipeline uses `CustomWordDetector` instead of the default `PIIDetector`.

**Key decisions:**
- `/replace` **replaces** automatic PII detection entirely (only user-specified words are removed)
- Multiple `/replace` messages **accumulate** words (don't overwrite)
- `/end` clears all custom words and restores default PII detection
- Comma-separated parsing: `roni 43` stays as one phrase (comma is the only delimiter)
- Case-insensitive matching
- 1-hour TTL per sender, refreshed on each `/replace` command
- Forward slash `/` prefix for commands (universal bot convention, no RTL issues)

---

## Tasks

### Task 1: Create CustomWordDetector

**Independent:** Yes
**Estimated scope:** Small (1 file)

**Files:**
- Create: `processor/custom_word_detector.py`
- Test: `tests/test_custom_word_detector.py`

**Steps:**

1. Create `processor/custom_word_detector.py` with class `CustomWordDetector` that inherits from `PIIDetector`:

```python
import re
from typing import Any
from processor.pii_detector import PIIDetector


class CustomWordDetector(PIIDetector):
    """Detects and removes user-specified custom words instead of NLP-based PII.

    Overrides PIIDetector to do case-insensitive plain text matching
    for a given list of words/phrases. No NLP models are loaded.
    """

    def __init__(self, words: list[str]) -> None:
        # Do NOT call super().__init__() — we don't want NLP models
        self._words = [w.strip() for w in words if w.strip()]

    def detect_entities(self, text: str) -> list[dict[str, Any]]:
        """Find all occurrences of custom words in text (case-insensitive)."""
        if not text.strip() or not self._words:
            return []

        entities = []
        text_lower = text.lower()
        for word in self._words:
            word_lower = word.lower()
            start = 0
            while True:
                idx = text_lower.find(word_lower, start)
                if idx == -1:
                    break
                entities.append({
                    "type": "CUSTOM_WORD",
                    "text": text[idx:idx + len(word)],
                    "start": idx,
                    "end": idx + len(word),
                    "score": 1.0,
                })
                start = idx + 1
        return entities

    def detect_and_remove(self, text: str) -> str:
        """Remove all custom words from text (case-insensitive)."""
        if not text.strip() or not self._words:
            return text

        result = text
        for word in self._words:
            pattern = re.compile(re.escape(word), re.IGNORECASE)
            result = pattern.sub("", result)

        # Clean up extra whitespace
        result = re.sub(r" {2,}", " ", result)
        result = re.sub(r" ([.,;:!?])", r"\1", result)
        return result.strip()
```

2. Write tests in `tests/test_custom_word_detector.py`:

```python
from processor.custom_word_detector import CustomWordDetector


def test_detect_entities_finds_custom_words():
    detector = CustomWordDetector(["Amit", "Joni"])
    entities = detector.detect_entities("Hello Amit and Joni are here")
    names = [e["text"] for e in entities]
    assert "Amit" in names
    assert "Joni" in names


def test_detect_entities_case_insensitive():
    detector = CustomWordDetector(["amit"])
    entities = detector.detect_entities("AMIT and Amit and amit")
    assert len(entities) == 3


def test_detect_and_remove_removes_words():
    detector = CustomWordDetector(["Amit", "Joni"])
    result = detector.detect_and_remove("Hello Amit and Joni are here")
    assert "Amit" not in result
    assert "Joni" not in result
    assert "Hello" in result


def test_detect_and_remove_case_insensitive():
    detector = CustomWordDetector(["amit"])
    result = detector.detect_and_remove("AMIT is here and amit too")
    assert "AMIT" not in result
    assert "amit" not in result


def test_multi_word_phrase():
    detector = CustomWordDetector(["roni 43"])
    result = detector.detect_and_remove("Address roni 43 is located here")
    assert "roni 43" not in result


def test_hebrew_words_replace():
    """Test /replace יעקב, יוסף — Hebrew words are detected and removed."""
    detector = CustomWordDetector(["יעקב", "יוסף"])
    text = "שלום יעקב ויוסף, מה שלומכם?"
    entities = detector.detect_entities(text)
    names = [e["text"] for e in entities]
    assert "יעקב" in names
    assert "יוסף" in names


def test_hebrew_words_removal():
    """Test that Hebrew words are fully removed from text."""
    detector = CustomWordDetector(["יעקב", "יוסף"])
    result = detector.detect_and_remove("שלום יעקב ויוסף נמצאים כאן")
    assert "יעקב" not in result
    assert "יוסף" not in result


def test_hebrew_and_english_mixed():
    """Test mixed Hebrew and English custom words."""
    detector = CustomWordDetector(["יעקב", "Amit"])
    text = "Hello Amit, שלום יעקב"
    result = detector.detect_and_remove(text)
    assert "Amit" not in result
    assert "יעקב" not in result


def test_hebrew_multi_word_phrase():
    """Test Hebrew multi-word phrase like a full name."""
    detector = CustomWordDetector(["יעקב כהן"])
    text = "שלום יעקב כהן, מה נשמע?"
    result = detector.detect_and_remove(text)
    assert "יעקב כהן" not in result


def test_empty_words_list():
    detector = CustomWordDetector([])
    result = detector.detect_and_remove("Hello world")
    assert result == "Hello world"


def test_empty_text():
    detector = CustomWordDetector(["Amit"])
    result = detector.detect_and_remove("")
    assert result == ""
```

3. Run: `pytest tests/test_custom_word_detector.py -v` → Expect: all PASS

**Verification:** `pytest tests/test_custom_word_detector.py -v`
**Acceptance criteria:**
- [ ] CustomWordDetector inherits from PIIDetector
- [ ] Does NOT load NLP models (no super().__init__())
- [ ] detect_entities() returns correct format matching PIIDetector's output
- [ ] detect_and_remove() removes words case-insensitively
- [ ] Hebrew words (`יעקב`, `יוסף`) are detected and removed correctly
- [ ] Mixed Hebrew + English words work
- [ ] Hebrew multi-word phrases work
- [ ] Multi-word English phrases (comma-separated) work

---

### Task 2: Create WordSessionStore

**Independent:** Yes
**Estimated scope:** Small (1 file)

**Files:**
- Create: `word_session_store.py`
- Test: `tests/test_word_session_store.py`

**Steps:**

1. Create `word_session_store.py`:

```python
import time


class WordSessionStore:
    """Per-sender custom word storage with 1-hour TTL.

    Words accumulate across multiple /replace commands.
    TTL is refreshed on each /replace command for that sender.
    """

    TTL_SECONDS = 3600  # 1 hour

    def __init__(self) -> None:
        # {sender: {"words": set[str], "expires_at": float}}
        self._sessions: dict[str, dict] = {}

    def add_words(self, sender: str, words: list[str]) -> list[str]:
        """Add words for a sender. Returns the full current word list."""
        self._prune_expired()
        cleaned = [w.strip() for w in words if w.strip()]
        if sender not in self._sessions:
            self._sessions[sender] = {"words": set(), "expires_at": 0}
        self._sessions[sender]["words"].update(cleaned)
        self._sessions[sender]["expires_at"] = time.time() + self.TTL_SECONDS
        return sorted(self._sessions[sender]["words"])

    def get_words(self, sender: str) -> list[str]:
        """Get active words for a sender. Returns empty list if expired/none."""
        self._prune_expired()
        session = self._sessions.get(sender)
        if not session:
            return []
        return sorted(session["words"])

    def clear(self, sender: str) -> None:
        """Clear all words for a sender (/end command)."""
        self._sessions.pop(sender, None)

    def has_active_session(self, sender: str) -> bool:
        """Check if sender has active custom words."""
        self._prune_expired()
        return sender in self._sessions and bool(self._sessions[sender]["words"])

    def _prune_expired(self) -> None:
        """Remove expired sessions."""
        now = time.time()
        expired = [s for s, d in self._sessions.items() if d["expires_at"] <= now]
        for s in expired:
            del self._sessions[s]
```

2. Write tests in `tests/test_word_session_store.py`:

```python
import time
from unittest.mock import patch
from word_session_store import WordSessionStore


def test_add_and_get_words():
    store = WordSessionStore()
    store.add_words("+972501234567", ["Amit", "Joni"])
    words = store.get_words("+972501234567")
    assert "Amit" in words
    assert "Joni" in words


def test_accumulate_words():
    store = WordSessionStore()
    store.add_words("+972501234567", ["Amit"])
    store.add_words("+972501234567", ["Joni"])
    words = store.get_words("+972501234567")
    assert "Amit" in words
    assert "Joni" in words


def test_accumulate_hebrew_words():
    """Test /replace יעקב then /replace יוסף accumulates both."""
    store = WordSessionStore()
    store.add_words("+972501234567", ["יעקב"])
    store.add_words("+972501234567", ["יוסף"])
    words = store.get_words("+972501234567")
    assert "יעקב" in words
    assert "יוסף" in words


def test_clear_words():
    store = WordSessionStore()
    store.add_words("+972501234567", ["Amit"])
    store.clear("+972501234567")
    assert not store.has_active_session("+972501234567")


def test_expiry():
    store = WordSessionStore()
    store.add_words("+972501234567", ["Amit"])
    # Simulate 1 hour + 1 second passing
    with patch("word_session_store.time") as mock_time:
        mock_time.time.return_value = time.time() + 3601
        assert not store.has_active_session("+972501234567")
        assert store.get_words("+972501234567") == []


def test_per_sender_isolation():
    store = WordSessionStore()
    store.add_words("sender1", ["Amit"])
    store.add_words("sender2", ["Joni"])
    assert store.get_words("sender1") == ["Amit"]
    assert store.get_words("sender2") == ["Joni"]


def test_duplicate_words_not_added():
    store = WordSessionStore()
    store.add_words("+972501234567", ["Amit", "Amit"])
    store.add_words("+972501234567", ["Amit"])
    words = store.get_words("+972501234567")
    assert words.count("Amit") == 1


def test_strips_whitespace():
    store = WordSessionStore()
    store.add_words("+972501234567", ["  Amit  ", " Joni"])
    words = store.get_words("+972501234567")
    assert "Amit" in words
    assert "Joni" in words
```

3. Run: `pytest tests/test_word_session_store.py -v` → Expect: all PASS

**Verification:** `pytest tests/test_word_session_store.py -v`
**Acceptance criteria:**
- [ ] Words accumulate across multiple add_words calls
- [ ] Hebrew words accumulate correctly
- [ ] clear() removes all words for a sender
- [ ] Words expire after 1 hour
- [ ] Per-sender isolation works
- [ ] Duplicates are not stored
- [ ] Whitespace is trimmed

---

### Task 3: Modify Pipeline to Accept Detector Override

**Independent:** Yes
**Estimated scope:** Small (1 file)

**Files:**
- Modify: `processor/pipeline.py` — add `detector` parameter to `process()`

**Steps:**

1. Modify `CleaningPipeline.process()` to accept an optional `detector` parameter:

```python
def process(self, input_path: Path, detector: PIIDetector | None = None) -> Path:
```

When `detector` is provided, create temporary cleaner instances using that detector instead of the default:

```python
if detector is not None:
    pdf_cleaner = PDFCleaner(detector)
    docx_cleaner = DOCXCleaner(detector)
else:
    pdf_cleaner = self._pdf_cleaner
    docx_cleaner = self._docx_cleaner
```

Then use `pdf_cleaner` / `docx_cleaner` in the routing logic instead of `self._pdf_cleaner` / `self._docx_cleaner`.

**Verification:** Existing tests still pass: `pytest tests/ -v`
**Acceptance criteria:**
- [ ] `process()` accepts optional `detector` parameter
- [ ] When None, uses default PIIDetector (existing behavior unchanged)
- [ ] When provided, uses the given detector for cleaning

---

### Task 4: Add /replace and /end Command Handling to SignalBot

**Independent:** No (depends on Tasks 1, 2, 3)
**Estimated scope:** Medium (1 file, significant changes)

**Files:**
- Modify: `signal_bot.py` — add command parsing, session store, detector swapping

**Steps:**

1. Import `CustomWordDetector` and `WordSessionStore` at top of `signal_bot.py`
2. Add `WordSessionStore` instance to `SignalBot.__init__`
3. Update `USAGE_MESSAGE` to mention `/replace` and `/end` commands
4. Modify `handle_message()` to check for commands BEFORE checking attachments:

```python
def handle_message(self, sender, message, attachments=None):
    # Allowlist check (existing)
    ...

    # Command handling
    if message and message.strip().startswith("/"):
        self._handle_command(sender, message.strip())
        # If message also has attachments, process them too
        if not attachments:
            return

    # Existing attachment handling...
    if not attachments:
        self._send_message(sender, USAGE_MESSAGE.format(...))
        return
    ...
```

5. Add `_handle_command()` method:

```python
def _handle_command(self, sender: str, message: str) -> None:
    if message.lower().startswith("/replace "):
        raw_words = message[len("/replace "):]
        words = [w.strip() for w in raw_words.split(",") if w.strip()]
        if not words:
            self._send_message(sender, "Usage: /replace word1, word2, word3")
            return
        all_words = self._word_store.add_words(sender, words)
        word_list = ", ".join(all_words)
        self._send_message(
            sender,
            f"Custom words set ({len(all_words)}): {word_list}\n"
            f"These words will be removed from documents sent in the next hour.\n"
            f"Send /end to clear."
        )
    elif message.lower().strip() == "/end":
        self._word_store.clear(sender)
        self._send_message(sender, "Custom words cleared. Back to automatic PII detection.")
    else:
        self._send_message(sender, "Unknown command. Available: /replace, /end")
```

6. Modify `_process_attachment()` to check for active session and pass detector:

```python
def _process_attachment(self, sender, attachment):
    ...
    # Before calling pipeline.process():
    detector = None
    if self._word_store.has_active_session(sender):
        from processor.custom_word_detector import CustomWordDetector
        detector = CustomWordDetector(self._word_store.get_words(sender))

    cleaned_path = self._pipeline.process(input_path, detector=detector)
    ...
```

**Verification:** `pytest tests/ -v` + manual testing via Signal
**Acceptance criteria:**
- [ ] `/replace Amit, Joni` stores words and sends confirmation with word list
- [ ] `/replace יעקב, יוסף` stores Hebrew words and sends confirmation
- [ ] `/replace roni 43` adds to existing words (accumulates)
- [ ] `/end` clears words and confirms
- [ ] Documents sent after `/replace` use CustomWordDetector
- [ ] Documents sent without active session use default PIIDetector
- [ ] Hebrew words in `/replace` work correctly
- [ ] Unknown `/` commands get helpful error message

---

### Task 5: Write Integration Tests

**Independent:** No (depends on Tasks 1-4)
**Estimated scope:** Small (1 file)

**Files:**
- Create: `tests/test_replace_command.py`

**Steps:**

1. Write integration tests covering the full flow:
   - `/replace` command parsing with English words
   - `/replace` command parsing with Hebrew words (`/replace יעקב, יוסף`)
   - Word accumulation across multiple `/replace` messages
   - `/end` command clearing words
   - Pipeline using CustomWordDetector when session active
   - Pipeline using default PIIDetector when no session

2. Run: `pytest tests/test_replace_command.py -v` → Expect: all PASS

**Verification:** `pytest tests/test_replace_command.py -v`
**Acceptance criteria:**
- [ ] Full command → process → clean flow tested
- [ ] Hebrew words tested (`/replace יעקב, יוסף`)
- [ ] Accumulation tested
- [ ] /end tested
- [ ] No regressions in existing tests

---

## Dependency Graph

```
Task 1 (CustomWordDetector) ──┐
                              │
Task 2 (WordSessionStore)  ───┼──► Task 4 (SignalBot commands) ──► Task 5 (Integration tests)
                              │
Task 3 (Pipeline override) ───┘
```

**Parallelizable:** Tasks 1, 2, 3
**Sequential:** Task 4 (after 1, 2, 3), Task 5 (after 4)

---

## Verification Summary

| Task | Verification Command | Expected Output |
|------|---------------------|-----------------|
| 1 | `pytest tests/test_custom_word_detector.py -v` | All tests pass |
| 2 | `pytest tests/test_word_session_store.py -v` | All tests pass |
| 3 | `pytest tests/ -v` | No regressions |
| 4 | `pytest tests/ -v` | All tests pass |
| 5 | `pytest tests/test_replace_command.py -v` | All tests pass |
