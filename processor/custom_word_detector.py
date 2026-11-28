import re
from collections import Counter
from typing import Any

from processor.pii_detector import PIIDetector

# One-letter Hebrew prefixes (and, the, in, to, from, that, as). A custom word
# also matches after up to two of them: "דני" matches "לדני" and "ושלדני".
# The prefix is kept in the text; only the word itself is removed.
_HEBREW_PREFIXES = "ובהלמשכ"
_HEBREW_LETTER = re.compile(r"[א-ת]")


def _word_pattern(word: str) -> re.Pattern[str]:
    """Whole-word, case-insensitive pattern for one custom word or phrase.

    "Dan" does not match inside "Jordan", and "43" not inside "1435".
    """
    prefix = f"[{_HEBREW_PREFIXES}]{{0,2}}" if _HEBREW_LETTER.match(word) else ""
    return re.compile(rf"(?<!\w)(?P<prefix>{prefix})(?P<word>{re.escape(word)})(?!\w)", re.IGNORECASE)


class CustomWordDetector(PIIDetector):
    """Removes user-specified words on top of automatic PII detection.

    ``base`` is the automatic detector (a loaded PIIDetector); its findings
    are kept and the custom words are added. With ``base=None`` only the
    custom words are detected (no NLP models are loaded either way).

    One instance is used per document: it counts what it finds, per custom
    word (``custom_hits``) and per automatic PII type (``auto_hits``), so the
    bot can tell the user what was removed without echoing any auto-detected
    value.
    """

    def __init__(self, words: list[str], base: PIIDetector | None = None) -> None:
        # Do NOT call super().__init__() — the NLP models live in ``base``.
        self._base = base
        self._words = [w.strip() for w in words if w.strip()]
        # Longest first, so "Dana Cohen" is removed whole before "Dana" alone.
        self._patterns = [(w, _word_pattern(w)) for w in sorted(self._words, key=len, reverse=True)]
        self.custom_hits: dict[str, int] = {w: 0 for w in self._words}
        self.auto_hits: Counter[str] = Counter()

    def detect_entities(self, text: str) -> list[dict[str, Any]]:
        """Automatic PII entities (if any base) plus every custom-word match."""
        if not text.strip():
            return []

        entities: list[dict[str, Any]] = []
        if self._base is not None:
            auto = self._base.detect_entities(text)
            self.auto_hits.update(e["type"] for e in auto)
            entities.extend(auto)

        for word, pattern in self._patterns:
            for m in pattern.finditer(text):
                self.custom_hits[word] += 1
                entities.append({
                    "type": "CUSTOM_WORD",
                    "text": m.group("word"),
                    "start": m.start("word"),
                    "end": m.end("word"),
                    "score": 1.0,
                })
        return entities

    def detect_and_remove(self, text: str) -> str:
        """Remove custom words, then automatic PII (if any base)."""
        if not text.strip():
            return text

        result = text
        for _, pattern in self._patterns:
            result = pattern.sub(lambda m: m.group("prefix"), result)

        if self._base is not None:
            return self._base.detect_and_remove(result)

        # Clean up extra whitespace (the base detector does the same)
        result = re.sub(r" {2,}", " ", result)
        result = re.sub(r" ([.,;:!?])", r"\1", result)
        return result.strip()
