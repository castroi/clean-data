import logging
import re
from typing import Any

from presidio_analyzer import (
    AnalyzerEngine,
    PatternRecognizer,
    Pattern,
)
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

logger = logging.getLogger(__name__)


def _build_israeli_id_recognizer() -> PatternRecognizer:
    """Recognizer for Israeli Teudat Zehut (9-digit ID)."""
    return PatternRecognizer(
        supported_entity="IL_ID_NUMBER",
        name="Israeli ID Recognizer",
        patterns=[Pattern("IL_ID", r"\b\d{9}\b", 0.6)],
        supported_language="en",
    )


def _build_israeli_phone_recognizer() -> PatternRecognizer:
    """Recognizer for Israeli phone numbers."""
    patterns = [
        Pattern("IL_PHONE_MOBILE", r"\b05\d-?\d{7}\b", 0.7),
        Pattern("IL_PHONE_INTL", r"\+972-?\d{1,2}-?\d{7}\b", 0.8),
        Pattern("IL_PHONE_LANDLINE", r"\b0[2-9]-?\d{7}\b", 0.5),
    ]
    return PatternRecognizer(
        supported_entity="PHONE_NUMBER",
        name="Israeli Phone Recognizer",
        patterns=patterns,
        supported_language="en",
    )


class PIIDetector:
    """Detects and removes PII using Presidio with English NLP and Israeli regex recognizers.

    Uses spaCy en_core_web_sm for English NER. Custom pattern recognizers cover
    Israeli Teudat Zehut (9-digit IDs) and Israeli phone number formats (05X and
    +972). No NLP model is loaded for Hebrew — Hebrew text still passes through
    the English analyzer and the regex recognizers.
    """

    def __init__(self) -> None:
        nlp_config = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
        nlp_engine = NlpEngineProvider(nlp_configuration=nlp_config).create_engine()
        self._analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
        self._anonymizer = AnonymizerEngine()

        # Register custom Israeli recognizers
        self._analyzer.registry.add_recognizer(_build_israeli_id_recognizer())
        self._analyzer.registry.add_recognizer(_build_israeli_phone_recognizer())

    def detect_entities(self, text: str) -> list[dict[str, Any]]:
        """Detect PII entities in text and return their details."""
        if not text.strip():
            return []

        # Run Presidio analyzer (English NLP + pattern recognizers)
        results = self._analyzer.analyze(
            text=text,
            language="en",
            entities=[
                "PERSON",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "LOCATION",
                "IL_ID_NUMBER",
                "CREDIT_CARD",
                "IBAN_CODE",
                "IP_ADDRESS",
                "URL",
            ],
        )

        return [
            {
                "type": r.entity_type,
                "text": text[r.start : r.end],
                "start": r.start,
                "end": r.end,
                "score": r.score,
            }
            for r in results
        ]

    def detect_and_remove(self, text: str) -> str:
        """Detect PII in text and remove it completely."""
        if not text.strip():
            return text

        # Run Presidio analyzer
        results = self._analyzer.analyze(
            text=text,
            language="en",
            entities=[
                "PERSON",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "LOCATION",
                "IL_ID_NUMBER",
                "CREDIT_CARD",
                "IBAN_CODE",
                "IP_ADDRESS",
                "URL",
            ],
        )

        if not results:
            return text

        # Anonymize — replace PII with empty string
        anonymized = self._anonymizer.anonymize(
            text=text,
            analyzer_results=results,
            operators={"DEFAULT": OperatorConfig("replace", {"new_value": ""})},
        )

        # Clean up extra whitespace
        cleaned = re.sub(r" {2,}", " ", anonymized.text)
        cleaned = re.sub(r" ([.,;:!?])", r"\1", cleaned)
        cleaned = cleaned.strip()

        return cleaned
