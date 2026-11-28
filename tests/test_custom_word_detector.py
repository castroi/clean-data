from processor.custom_word_detector import CustomWordDetector


class FakeBase:
    """Stands in for the loaded PIIDetector: finds one email, removes it."""

    EMAIL = "a@b.com"

    def detect_entities(self, text):
        i = text.find(self.EMAIL)
        if i == -1:
            return []
        return [{"type": "EMAIL_ADDRESS", "text": self.EMAIL, "start": i, "end": i + len(self.EMAIL), "score": 1.0}]

    def detect_and_remove(self, text):
        return " ".join(text.replace(self.EMAIL, "").split())


# --- matching ------------------------------------------------------------------


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


def test_whole_words_only():
    """"Dan" must not cut into "Jordan" or "Danger"; "43" not into "1435"."""
    detector = CustomWordDetector(["Dan", "43"])
    text = "Dan met Jordan. Danger at 1435, room 43."
    assert detector.detect_and_remove(text) == "met Jordan. Danger at 1435, room."
    assert [e["text"] for e in detector.detect_entities(text)] == ["Dan", "43"]


def test_word_next_to_punctuation_matches():
    detector = CustomWordDetector(["Amit"])
    assert detector.detect_and_remove("(Amit), Amit. Amit's") == "(),. 's"


def test_longer_phrase_wins_over_its_part():
    detector = CustomWordDetector(["Dana", "Dana Cohen"])
    assert detector.detect_and_remove("Signed: Dana Cohen") == "Signed:"


def test_phrase_matches_across_line_break_and_extra_spaces():
    """PDF text often breaks a name over two lines."""
    detector = CustomWordDetector(["Dana Cohen"])
    for text in ["Dana\nCohen signed", "Dana  Cohen signed", "Dana\u00a0Cohen signed"]:
        assert detector.detect_and_remove(text) == "signed", repr(text)


def test_word_with_extra_spaces_is_normalized():
    detector = CustomWordDetector(["  Dana   Cohen "])
    assert detector.words == ["Dana Cohen"]
    assert detector.detect_and_remove("Dana Cohen signed") == "signed"


def test_nfc_and_nfd_forms_match():
    import unicodedata
    nfd = unicodedata.normalize("NFD", "José")
    assert CustomWordDetector(["José"]).detect_and_remove(f"{nfd} signed") == "signed"
    assert CustomWordDetector([nfd]).detect_and_remove("José signed") == "signed"


def test_symbols_in_word_are_literal():
    detector = CustomWordDetector(["054-123.4567", "a+b"])
    assert detector.detect_and_remove("call 054-123.4567 or 054x123x4567, a+b") == "call or 054x123x4567,"


# --- Hebrew ----------------------------------------------------------------------


def test_hebrew_words_detected():
    detector = CustomWordDetector(["יעקב", "יוסף"])
    entities = detector.detect_entities("שלום יעקב ויוסף, מה שלומכם?")
    names = [e["text"] for e in entities]
    assert "יעקב" in names
    assert "יוסף" in names


def test_hebrew_prefix_letters_match_and_are_kept():
    """"דני" also matches after ו/ל/ש... prefixes; the prefix letter stays."""
    detector = CustomWordDetector(["דני"])
    result = detector.detect_and_remove("דני אמר לדני ושדני הלך")
    assert "דני" not in result
    assert result == "אמר ל וש הלך"
    assert len(detector.detect_entities("דני אמר לדני ושדני הלך")) == 3


def test_three_hebrew_prefix_letters():
    detector = CustomWordDetector(["דני"])
    assert detector.detect_and_remove("ושלדני בא") == "ושל בא"
    assert [e["text"] for e in detector.detect_entities("ושלדני בא")] == ["דני"]


def test_four_letters_before_word_not_treated_as_prefixes():
    detector = CustomWordDetector(["דני"])
    assert detector.detect_and_remove("ושלבדני") == "ושלבדני"


def test_hebrew_word_inside_longer_word_not_matched():
    detector = CustomWordDetector(["דני"])
    assert detector.detect_and_remove("דניאל בא") == "דניאל בא"


def test_hebrew_word_that_starts_with_a_prefix_letter():
    """"משה" starts with מ (a prefix letter) and must still match on its own."""
    detector = CustomWordDetector(["משה"])
    assert detector.detect_and_remove("משה ולמשה") == "ול"


def test_hebrew_multi_word_phrase():
    detector = CustomWordDetector(["יעקב כהן"])
    result = detector.detect_and_remove("שלום יעקב כהן, מה נשמע?")
    assert "יעקב כהן" not in result


def test_hebrew_and_english_mixed():
    detector = CustomWordDetector(["יעקב", "Amit"])
    result = detector.detect_and_remove("Hello Amit, שלום יעקב")
    assert "Amit" not in result
    assert "יעקב" not in result


# --- additive to automatic PII, counting ---------------------------------------


def test_automatic_pii_still_removed_with_custom_words():
    detector = CustomWordDetector(["Amit"], base=FakeBase())
    result = detector.detect_and_remove("Amit wrote to a@b.com today")
    assert "Amit" not in result
    assert "a@b.com" not in result
    assert "today" in result


class NameBase:
    """Automatic detector that only recognises the full name "Amit Levi"."""

    def detect_entities(self, text):
        i = text.find("Amit Levi")
        return [] if i == -1 else [{"type": "PERSON", "text": "Amit Levi", "start": i, "end": i + 9, "score": 0.9}]

    def detect_and_remove(self, text):
        raise AssertionError("not used when there are custom words")


def test_custom_words_do_not_hide_names_from_automatic_detection():
    """Both are found on the original text: removing "Amit" first would have
    left "Levi" unrecognised."""
    detector = CustomWordDetector(["Amit"], base=NameBase())
    assert detector.detect_and_remove("Signed by Amit Levi today") == "Signed by today"


def test_overlapping_matches_removed_once():
    detector = CustomWordDetector(["Dana", "Dana Cohen", "Cohen"])
    assert detector.detect_and_remove("Dana Cohen, Cohen.") == ",."


def test_base_alone_when_no_custom_words():
    detector = CustomWordDetector([], base=FakeBase())
    assert detector.detect_and_remove("write a@b.com") == "write"
    assert [e["type"] for e in detector.detect_entities("write a@b.com")] == ["EMAIL_ADDRESS"]


def test_entities_include_automatic_and_custom():
    detector = CustomWordDetector(["Amit", "Ruti"], base=FakeBase())
    entities = detector.detect_entities("Amit, a@b.com")
    assert sorted(e["type"] for e in entities) == ["CUSTOM_WORD", "EMAIL_ADDRESS"]


def test_words_deduplicated_case_insensitively():
    assert CustomWordDetector(["Amit", "amit", " AMIT "]).words == ["Amit"]


# --- edge cases -----------------------------------------------------------------


def test_empty_words_list():
    detector = CustomWordDetector([])
    assert detector.detect_and_remove("Hello world") == "Hello world"


def test_empty_text():
    detector = CustomWordDetector(["Amit"])
    assert detector.detect_and_remove("") == ""
    assert detector.detect_entities("   ") == []
