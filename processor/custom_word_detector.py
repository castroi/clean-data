import re
import unicodedata
from collections import Counter
from typing import Any

from processor.pii_detector import PIIDetector

# One-letter Hebrew prefixes (and, the, in, to, from, that, as). A custom word
# also matches after up to three of them: "דני" matches "לדני" and "ושלדני".
# The prefix is kept in the text; only the word itself is removed.
_HEBREW_PREFIXES = "ובהלמשכ"
_MAX_PREFIXES = 3
_HEBREW_LETTER = re.compile(r"[א-ת]")


def normalize_word(word: str) -> str:
    """Canonical form of a custom word: NFC, inner whitespace collapsed."""
    return " ".join(unicodedata.normalize("NFC", word).split())


def _word_pattern(word: str) -> re.Pattern[str]:
    """Whole-word, case-insensitive pattern for one custom word or phrase.

    "Dan" does not match inside "Jordan", and "43" not inside "1435". The
    parts of a phrase match across any whitespace (double space, line break,
    non-breaking space), as PDF text often breaks a name over two lines.
    """
    prefix = f"[{_HEBREW_PREFIXES}]{{0,{_MAX_PREFIXES}}}" if _HEBREW_LETTER.match(word) else ""
    body = r"\s+".join(re.escape(part) for part in word.split())
    return re.compile(rf"(?<!\w)(?P<prefix>{prefix})(?P<word>{body})(?!\w)", re.IGNORECASE)


def _tidy(text: str) -> str:
    """Whitespace cleanup after deletion (same rules as PIIDetector)."""
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r" ([.,;:!?])", r"\1", text)
    return text.strip()


class CustomWordDetector(PIIDetector):
    """Removes user-specified words on top of automatic PII detection.

    ``base`` is the automatic detector (a loaded PIIDetector); its findings
    are kept and the custom words are added. With ``base=None`` only the
    custom words are detected (no NLP models are loaded either way).

    Both kinds of match are found on the same original text and removed in
    one pass, so removing a custom word never hides PII from the automatic
    detector (e.g. "Levi" in "Amit Levi" once "Amit" is gone).

    One instance is used per document: it counts what it finds, per custom
    word (``custom_hits``) and per automatic PII type (``auto_hits``), so the
    bot can tell the user what was removed without echoing any auto-detected
    value. Text is NFC-normalized before matching.
    """

    def __init__(self, words: list[str], base: PIIDetector | None = None) -> None:
        # Do NOT call super().__init__() — the NLP models live in ``base``.
        self._base = base
        self._words: list[str] = []
        for w in words:
            w = normalize_word(w)
            if w and w.lower() not in (x.lower() for x in self._words):
                self._words.append(w)
        self._patterns = [(w, _word_pattern(w)) for w in self._words]
        self.custom_hits: dict[str, int] = {w: 0 for w in self._words}
        self.auto_hits: Counter[str] = Counter()

    def _find(self, text: str) -> tuple[list[dict[str, Any]], list[tuple[str, dict[str, Any]]]]:
        """(automatic entities, [(custom word, entity)]) on ``text``; no counting."""
        auto = self._base.detect_entities(text) if self._base is not None else []
        custom = [
            (word, {
                "type": "CUSTOM_WORD",
                "text": m.group("word"),
                "start": m.start("word"),
                "end": m.end("word"),
                "score": 1.0,
            })
            for word, pattern in self._patterns
            for m in pattern.finditer(text)
        ]
        return auto, custom

    def detect_entities(self, text: str) -> list[dict[str, Any]]:
        """Automatic PII entities (if any base) plus every custom-word match."""
        if not text.strip():
            return []
        text = unicodedata.normalize("NFC", text)
        auto, custom = self._find(text)
        self.auto_hits.update(e["type"] for e in auto)
        for word, _ in custom:
            self.custom_hits[word] += 1
        return auto + [e for _, e in custom]

    def detect_and_remove(self, text: str) -> str:
        """Remove custom words and automatic PII, found on the same text."""
        if not text.strip():
            return text
        if not self._patterns:
            # No custom words: exactly the automatic detector's behaviour.
            return self._base.detect_and_remove(text) if self._base is not None else text

        text = unicodedata.normalize("NFC", text)
        auto, custom = self._find(text)
        spans = sorted((e["start"], e["end"]) for e in auto + [e for _, e in custom])

        # Merge overlapping spans, then delete them in one pass.
        merged: list[list[int]] = []
        for start, end in spans:
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        pieces, pos = [], 0
        for start, end in merged:
            pieces.append(text[pos:start])
            pos = end
        pieces.append(text[pos:])
        return _tidy("".join(pieces))
