import pytest

from processor.pii_detector import PIIDetector


@pytest.fixture(scope="module")
def detector():
    return PIIDetector()


def test_removes_english_name(detector: PIIDetector):
    result = detector.detect_and_remove("Contact John Smith for details")
    assert "John" not in result
    assert "Smith" not in result


def test_removes_email(detector: PIIDetector):
    result = detector.detect_and_remove("Email: john@example.com")
    assert "john@example.com" not in result


def test_removes_israeli_id(detector: PIIDetector):
    result = detector.detect_and_remove("ID number: 123456782")
    assert "123456782" not in result


def test_removes_israeli_phone(detector: PIIDetector):
    result = detector.detect_and_remove("Call 052-1234567")
    assert "052-1234567" not in result


def test_removes_phone_plus972(detector: PIIDetector):
    result = detector.detect_and_remove("Phone: +972521234567")
    assert "+972521234567" not in result


def test_preserves_non_pii_text(detector: PIIDetector):
    result = detector.detect_and_remove("The weather is nice today")
    assert "weather" in result
    assert "nice" in result


def test_detect_entities_returns_list(detector: PIIDetector):
    entities = detector.detect_entities("Email: john@example.com")
    assert isinstance(entities, list)
    assert len(entities) > 0
    assert "type" in entities[0]


def test_handles_empty_string(detector: PIIDetector):
    result = detector.detect_and_remove("")
    assert result == ""


def test_detect_entities_empty_string(detector: PIIDetector):
    entities = detector.detect_entities("")
    assert entities == []


def test_no_transformers_import_at_module_load():
    """Regression guard: importing pii_detector must not pull in transformers/torch.

    These libraries were removed to strip the Hebrew NLP pipeline. If a future
    change reintroduces them as a top-level or constructor-time import, this
    test will fail and force an explicit decision.
    """
    import sys

    for mod in list(sys.modules):
        if mod.startswith(("transformers", "torch", "langdetect")):
            del sys.modules[mod]
    if "processor.pii_detector" in sys.modules:
        del sys.modules["processor.pii_detector"]

    import processor.pii_detector  # noqa: F401
    from processor.pii_detector import PIIDetector

    PIIDetector()  # Constructor must also not import transformers/torch

    assert "transformers" not in sys.modules, \
        "transformers must not be imported by pii_detector"
    assert "torch" not in sys.modules, \
        "torch must not be imported by pii_detector"
    assert "langdetect" not in sys.modules, \
        "langdetect must not be imported by pii_detector"
