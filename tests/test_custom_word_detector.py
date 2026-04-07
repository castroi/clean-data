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
