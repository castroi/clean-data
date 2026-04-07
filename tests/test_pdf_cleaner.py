import pytest
from pathlib import Path

import fitz

from processor.pii_detector import PIIDetector
from processor.pdf_cleaner import PDFCleaner


@pytest.fixture(scope="module")
def detector():
    return PIIDetector()


@pytest.fixture(scope="module")
def cleaner(detector):
    return PDFCleaner(detector)


def _create_test_pdf(path: Path, text: str, author: str = "John Smith") -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), text)
    doc.set_metadata({"author": author, "title": "Secret Report"})
    doc.save(str(path))
    doc.close()


def test_removes_pii_from_pdf(cleaner: PDFCleaner, tmp_path: Path):
    input_path = tmp_path / "input.pdf"
    output_path = tmp_path / "output.pdf"
    _create_test_pdf(input_path, "Contact John Smith at john@example.com")

    stats = cleaner.clean(input_path, output_path)

    doc = fitz.open(str(output_path))
    text = doc[0].get_text()
    doc.close()

    assert "John" not in text
    assert "john@example.com" not in text
    assert stats["pii_items_removed"] > 0


def test_strips_metadata(cleaner: PDFCleaner, tmp_path: Path):
    input_path = tmp_path / "input.pdf"
    output_path = tmp_path / "output.pdf"
    _create_test_pdf(input_path, "Hello world")

    cleaner.clean(input_path, output_path)

    doc = fitz.open(str(output_path))
    metadata = doc.metadata
    doc.close()

    assert metadata["author"] == ""
    assert metadata["title"] == ""


def test_returns_stats(cleaner: PDFCleaner, tmp_path: Path):
    input_path = tmp_path / "input.pdf"
    output_path = tmp_path / "output.pdf"
    _create_test_pdf(input_path, "Email: test@example.com")

    stats = cleaner.clean(input_path, output_path)

    assert "pages_processed" in stats
    assert "pii_items_removed" in stats
    assert "metadata_stripped" in stats
    assert stats["metadata_stripped"] is True


def test_output_is_valid_pdf(cleaner: PDFCleaner, tmp_path: Path):
    input_path = tmp_path / "input.pdf"
    output_path = tmp_path / "output.pdf"
    _create_test_pdf(input_path, "Contact John Smith")

    cleaner.clean(input_path, output_path)

    assert output_path.exists()
    doc = fitz.open(str(output_path))
    assert len(doc) == 1
    doc.close()


def test_preserves_non_pii_text(cleaner: PDFCleaner, tmp_path: Path):
    input_path = tmp_path / "input.pdf"
    output_path = tmp_path / "output.pdf"
    _create_test_pdf(input_path, "The weather is nice today")

    cleaner.clean(input_path, output_path)

    doc = fitz.open(str(output_path))
    text = doc[0].get_text()
    doc.close()

    assert "weather" in text
    assert "nice" in text


def test_removes_israeli_id_from_pdf(cleaner: PDFCleaner, tmp_path: Path):
    input_path = tmp_path / "input.pdf"
    output_path = tmp_path / "output.pdf"
    _create_test_pdf(input_path, "ID number: 123456782")

    cleaner.clean(input_path, output_path)

    doc = fitz.open(str(output_path))
    text = doc[0].get_text()
    doc.close()

    assert "123456782" not in text
