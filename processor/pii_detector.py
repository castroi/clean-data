import logging
import re
from typing import Any

from presidio_analyzer import (
    AnalyzerEngine,
    PatternRecognizer,
    Pattern,
    RecognizerResult,
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
    """Detects and removes PII using Presidio with English NLP support.

    Hebrew NER via heBERT is optional — if the model is not downloaded,
    Hebrew name detection is skipped but pattern-based detection still works.
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

        # Try loading Hebrew NER model
        self._hebrew_available = False
        self._hebrew_pipeline = None
        try:
            from transformers import pipeline

            self._hebrew_pipeline = pipeline(
                "ner",
                model="avichr/heBERT_NER",
                aggregation_strategy="simple",
            )
            self._hebrew_available = True
            logger.info("Hebrew NER model loaded successfully")
        except Exception as e:
            logger.warning("Hebrew NER model not available: %s", e)

    def _detect_language(self, text: str) -> str:
        """Detect the primary language of the text."""
        try:
            from langdetect import detect

            return detect(text)
        except Exception:
            return "en"

    def _get_hebrew_entities(self, text: str) -> list[RecognizerResult]:
        """Run Hebrew NER to find person names and locations."""
        if not self._hebrew_available or not self._hebrew_pipeline:
            return []

        results: list[RecognizerResult] = []
        try:
            ner_results = self._hebrew_pipeline(text)
            for entity in ner_results:
                entity_type = entity.get("entity_group", "")
                if entity_type in ("PER", "PERSON", "LOC", "LOCATION", "ORG"):
                    presidio_type = {
                        "PER": "PERSON",
                        "PERSON": "PERSON",
                        "LOC": "LOCATION",
                        "LOCATION": "LOCATION",
                        "ORG": "ORGANIZATION",
                    }.get(entity_type, "PERSON")
                    results.append(
                        RecognizerResult(
                            entity_type=presidio_type,
                            start=entity["start"],
                            end=entity["end"],
                            score=float(entity.get("score", 0.85)),
                        )
                    )
        except Exception as e:
            logger.warning("Hebrew NER failed: %s", e)

        return results

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

        # Add Hebrew NER results if text contains Hebrew
        lang = self._detect_language(text)
        if lang == "he":
            hebrew_entities = self._get_hebrew_entities(text)
            results.extend(hebrew_entities)

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

        # Add Hebrew entities
        lang = self._detect_language(text)
        if lang == "he":
            hebrew_entities = self._get_hebrew_entities(text)
            results.extend(hebrew_entities)

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
