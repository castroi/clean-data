import logging
from pathlib import Path

import fitz

from processor.metadata import strip_pdf_metadata
from processor.pii_detector import PIIDetector

logger = logging.getLogger(__name__)


class PDFCleaner:
    """Cleans PII from PDF documents and strips metadata."""

    def __init__(self, pii_detector: PIIDetector) -> None:
        self._detector = pii_detector

    def clean(self, input_path: Path, output_path: Path) -> dict:
        """Clean PII from a PDF file and save the result.

        Returns stats dict with pages_processed, pii_items_removed,
        and metadata_stripped.
        """
        doc = fitz.open(str(input_path))
        total_pii_removed = 0

        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text()

            if not text.strip():
                logger.warning(
                    "Page %d has no extractable text (may be scanned/image-based)",
                    page_num + 1,
                )
                continue

            # Detect PII entities
            entities = self._detector.detect_entities(text)
            if not entities:
                continue

            total_pii_removed += len(entities)

            # Redact each entity by finding and covering its text on the page
            for entity in entities:
                entity_text = entity["text"]
                # Search for the text on the page
                text_instances = page.search_for(entity_text)
                for inst in text_instances:
                    # Add redaction annotation (white fill to remove text)
                    page.add_redact_annot(inst, fill=(1, 1, 1))

            # Apply all redactions on the page
            page.apply_redactions()

        # Strip all metadata
        strip_pdf_metadata(doc)

        pages_processed = len(doc)
        doc.save(str(output_path), garbage=4, deflate=True)
        doc.close()

        logger.info(
            "PDF cleaned: %d pages, %d PII items removed",
            pages_processed,
            total_pii_removed,
        )

        return {
            "pages_processed": pages_processed,
            "pii_items_removed": total_pii_removed,
            "metadata_stripped": True,
        }
