import logging

import fitz
from docx import Document
from lxml import etree

logger = logging.getLogger(__name__)

_EMPTY_PDF_METADATA = {
    "author": "",
    "title": "",
    "subject": "",
    "keywords": "",
    "creator": "",
    "producer": "",
    "creationDate": "",
    "modDate": "",
    "trapped": "",
}


def strip_pdf_metadata(doc: fitz.Document) -> None:
    """Clear all metadata fields from a PDF document."""
    doc.set_metadata(_EMPTY_PDF_METADATA)
    logger.debug("PDF metadata stripped")


def strip_docx_metadata(doc: Document) -> None:
    """Clear all core properties from a DOCX document."""
    props = doc.core_properties
    props.author = ""
    props.last_modified_by = ""
    props.title = ""
    props.subject = ""
    props.keywords = ""
    props.category = ""
    props.comments = ""
    props.revision = 1
    # Remove date elements directly from XML since python-docx
    # doesn't allow setting them to None
    core_el = props._element
    for tag in ("dcterms:created", "dcterms:modified"):
        ns_tag = tag.split(":")
        nsmap = {
            "dcterms": "http://purl.org/dc/terms/",
        }
        found = core_el.findall(f"{{{nsmap['dcterms']}}}{ns_tag[1]}")
        for el in found:
            core_el.remove(el)
    logger.debug("DOCX metadata stripped")
