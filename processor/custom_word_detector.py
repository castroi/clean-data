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
