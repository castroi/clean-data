from pathlib import Path

import fitz
from docx import Document

from processor.metadata import strip_pdf_metadata, strip_docx_metadata


def test_strips_pdf_metadata():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Hello")
    doc.set_metadata({
        "author": "John Smith",
        "title": "Secret Report",
        "subject": "Confidential",
        "keywords": "secret,private",
        "creator": "Microsoft Word",
        "producer": "PDF Library",
    })
    strip_pdf_metadata(doc)
    meta = doc.metadata
    assert meta["author"] == ""
    assert meta["title"] == ""
    assert meta["subject"] == ""
    assert meta["keywords"] == ""
    assert meta["creator"] == ""
    assert meta["producer"] == ""
    doc.close()


def test_strips_docx_metadata():
    doc = Document()
    doc.add_paragraph("Hello")
    doc.core_properties.author = "John Smith"
    doc.core_properties.title = "Secret Report"
    doc.core_properties.subject = "Confidential"
    doc.core_properties.keywords = "secret,private"
    doc.core_properties.category = "Internal"
    doc.core_properties.comments = "Do not share"
    doc.core_properties.last_modified_by = "Jane Doe"
    strip_docx_metadata(doc)
    assert doc.core_properties.author in (None, "")
    assert doc.core_properties.title in (None, "")
    assert doc.core_properties.subject in (None, "")
    assert doc.core_properties.keywords in (None, "")
    assert doc.core_properties.category in (None, "")
    assert doc.core_properties.comments in (None, "")
    assert doc.core_properties.last_modified_by in (None, "")


def test_strips_pdf_dates():
    doc = fitz.open()
    doc.new_page()
    doc.set_metadata({
        "author": "Test",
        "creationDate": "D:20240101120000",
        "modDate": "D:20240101120000",
    })
    strip_pdf_metadata(doc)
    meta = doc.metadata
    assert meta["creationDate"] == ""
    assert meta["modDate"] == ""
    doc.close()
