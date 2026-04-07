# Clean-Data: Document PII Remover — Implementation Plan

**Goal:** Build a Python service that receives PDF/DOCX files via Signal, removes all personal data (names, IDs, addresses, metadata) using Microsoft Presidio + Hebrew NER, and returns the cleaned document — with zero data retention.

**Architecture:** Single Python monolith running a Signal bot listener and document processing pipeline. All NLP models run locally (no external API calls). Supports English and Hebrew (RTL). Documents are securely deleted after processing.

**Key decisions:**
- Microsoft Presidio for PII detection — extensible, open-source, runs locally
- HuggingFace heBERT-NER for Hebrew named entity recognition — local model, no API costs
- Signal via signal-cli for messaging — E2E encrypted, privacy-first
- Full PII removal (not redaction/placeholders) — cleanest output
- Zero data retention — files deleted immediately after sending cleaned version

---

## Tasks

### Task 1: Project Scaffolding & Dependencies

**Independent:** Yes
**Estimated scope:** Small (2-3 files)

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `config.py`
- Create: `.gitignore`

**Steps:**
1. Initialize the project directory structure:
   ```
   ~/clean-data/
   ├── processor/
   │   └── __init__.py
   ├── utils/
   │   └── __init__.py
   ├── tests/
   │   └── fixtures/
   └── docs/
   ```

2. Create `requirements.txt`:
   ```
   PyMuPDF>=1.23.0
   python-docx>=1.1.0
   presidio-analyzer>=2.2.0
   presidio-anonymizer>=2.2.0
   spacy>=3.7.0
   transformers>=4.36.0
   torch>=2.1.0
   pysignalclirestapi>=0.4.0
   python-dotenv>=1.0.0
   pytest>=7.4.0
   langdetect>=1.0.9
   ```

3. Create `.env.example`:
   ```
   SIGNAL_PHONE_NUMBER=+972XXXXXXXXX
   SIGNAL_CLI_URL=http://localhost:8080
   TEMP_DIR=/tmp/clean-data
   MAX_FILE_SIZE_MB=25
   PROCESSING_TIMEOUT=300
   ```

4. Create `config.py` — loads env vars with defaults using python-dotenv

5. Create `.gitignore` — exclude `.env`, `__pycache__`, `.pytest_cache`, temp files, model caches

6. Run: `pip install -r requirements.txt` → Expect: all packages installed
7. Run: `python -c "import fitz, docx, presidio_analyzer; print('OK')"` → Expect: `OK`

**Verification:** `python -c "from config import Config; print(Config.TEMP_DIR)"`
**Acceptance criteria:**
- [ ] All dependencies install successfully
- [ ] Config loads from .env with sensible defaults
- [ ] .gitignore covers sensitive and generated files

---

### Task 2: PII Detection Engine (Presidio + Hebrew NER)

**Independent:** Yes (can parallelize with Task 1 after deps exist)
**Estimated scope:** Medium (2-3 files)

**Files:**
- Create: `processor/pii_detector.py`
- Create: `tests/test_pii_detector.py`

**Steps:**
1. Write `processor/pii_detector.py`:
   - Class `PIIDetector` that wraps Presidio Analyzer + Anonymizer
   - On init: load spaCy English model (`en_core_web_lg`) and HuggingFace Hebrew NER model (`avichr/heBERT-NER`)
   - Add custom Presidio recognizers for:
     - Israeli ID (Teudat Zehut) — 9-digit pattern with check digit validation
     - Israeli phone numbers — 05X-XXXXXXX, +972-XX-XXXXXXX formats
     - Israeli addresses — common Hebrew address patterns (רחוב, שדרות, etc.)
   - Method `detect_and_remove(text: str) -> str`:
     - Detect language using `langdetect`
     - Run Presidio Analyzer with appropriate NLP engine (English or Hebrew)
     - Run Presidio Anonymizer with "remove" operator (replace PII with empty string)
     - Clean up extra whitespace left by removals
     - Return cleaned text
   - Method `detect_entities(text: str) -> list`:
     - Returns list of detected PII entities with types and positions (for testing/debugging)

2. Write failing tests in `tests/test_pii_detector.py`:
   ```python
   def test_removes_english_name():
       result = detector.detect_and_remove("Contact John Smith for details")
       assert "John" not in result
       assert "Smith" not in result

   def test_removes_israeli_id():
       result = detector.detect_and_remove("ID number: 123456782")
       assert "123456782" not in result

   def test_removes_hebrew_name():
       result = detector.detect_and_remove("יש ליצור קשר עם דוד כהן בנושא")
       assert "דוד" not in result
       assert "כהן" not in result

   def test_removes_israeli_phone():
       result = detector.detect_and_remove("Call 052-1234567")
       assert "052-1234567" not in result

   def test_removes_email():
       result = detector.detect_and_remove("Email: john@example.com")
       assert "john@example.com" not in result

   def test_mixed_hebrew_english():
       text = "שלום David Cohen, ID: 123456782, phone: 052-1234567"
       result = detector.detect_and_remove(text)
       assert "David" not in result
       assert "Cohen" not in result
       assert "123456782" not in result

   def test_preserves_non_pii_text():
       result = detector.detect_and_remove("The weather is nice today")
       assert "weather" in result
       assert "nice" in result
   ```

3. Run: `pytest tests/test_pii_detector.py -v` → Expect: FAIL (module doesn't exist yet)
4. Implement `PIIDetector` class
5. Run: `pytest tests/test_pii_detector.py -v` → Expect: PASS
6. Download required models:
   ```bash
   python -m spacy download en_core_web_lg
   python -c "from transformers import AutoTokenizer, AutoModelForTokenClassification; AutoTokenizer.from_pretrained('avichr/heBERT-NER'); AutoModelForTokenClassification.from_pretrained('avichr/heBERT-NER')"
   ```

**Verification:** `pytest tests/test_pii_detector.py -v`
**Acceptance criteria:**
- [ ] English PII detected and removed (names, emails, phones, addresses)
- [ ] Hebrew PII detected and removed (names, Israeli IDs, Israeli phones)
- [ ] Mixed Hebrew/English documents handled correctly
- [ ] Non-PII text preserved
- [ ] All processing runs locally — no external API calls

---

### Task 3: PDF Cleaner

**Independent:** No — depends on Task 2 (PIIDetector)
**Estimated scope:** Medium (2 files)

**Files:**
- Create: `processor/pdf_cleaner.py`
- Create: `tests/test_pdf_cleaner.py`
- Create: `tests/fixtures/sample_with_pii.pdf` (generated in test setup)

**Steps:**
1. Write `processor/pdf_cleaner.py`:
   - Class `PDFCleaner` that takes a `PIIDetector` instance
   - Method `clean(input_path: str, output_path: str) -> dict`:
     - Open PDF with PyMuPDF
     - For each page: extract text blocks, run through PIIDetector, redact PII
     - Strip all metadata (author, title, subject, creator, producer, dates)
     - Save cleaned PDF to output_path
     - Return stats: `{ pages_processed, pii_items_removed, metadata_stripped: True }`
   - Handle RTL (Hebrew) text correctly — PyMuPDF preserves text direction
   - Handle scanned/image-based PDFs: detect if page has no extractable text, log warning (OCR support deferred to future iteration)

2. Write tests in `tests/test_pdf_cleaner.py`:
   - Use PyMuPDF to programmatically generate test PDFs with known PII
   ```python
   def create_test_pdf(path, text):
       doc = fitz.open()
       page = doc.new_page()
       page.insert_text((50, 50), text)
       doc.set_metadata({
           "author": "John Smith",
           "title": "Secret Report"
       })
       doc.save(path)
       doc.close()

   def test_removes_pii_from_pdf():
       create_test_pdf(input_path, "Contact John Smith at john@example.com")
       cleaner.clean(input_path, output_path)
       # Read output and verify PII removed
       doc = fitz.open(output_path)
       text = doc[0].get_text()
       assert "John" not in text
       assert "john@example.com" not in text

   def test_strips_metadata():
       create_test_pdf(input_path, "Hello world")
       cleaner.clean(input_path, output_path)
       doc = fitz.open(output_path)
       metadata = doc.metadata
       assert metadata["author"] == ""
       assert metadata["title"] == ""

   def test_hebrew_rtl_pdf():
       # Create PDF with Hebrew text containing PII
       create_test_pdf(input_path, "שלום דוד כהן מרחוב הרצל 5 תל אביב")
       cleaner.clean(input_path, output_path)
       doc = fitz.open(output_path)
       text = doc[0].get_text()
       assert "דוד" not in text
       assert "כהן" not in text
   ```

3. Run: `pytest tests/test_pdf_cleaner.py -v` → Expect: FAIL
4. Implement `PDFCleaner`
5. Run: `pytest tests/test_pdf_cleaner.py -v` → Expect: PASS

**Verification:** `pytest tests/test_pdf_cleaner.py -v`
**Acceptance criteria:**
- [ ] PII removed from PDF text content
- [ ] All metadata stripped
- [ ] RTL Hebrew text handled correctly
- [ ] Output is a valid PDF
- [ ] Stats returned (pages processed, PII items found)

---

### Task 4: DOCX Cleaner

**Independent:** No — depends on Task 2 (PIIDetector)
**Estimated scope:** Medium (2 files)

**Files:**
- Create: `processor/docx_cleaner.py`
- Create: `tests/test_docx_cleaner.py`

**Steps:**
1. Write `processor/docx_cleaner.py`:
   - Class `DOCXCleaner` that takes a `PIIDetector` instance
   - Method `clean(input_path: str, output_path: str) -> dict`:
     - Open DOCX with python-docx
     - Walk through: paragraphs, tables (all cells), headers, footers
     - For each text run: detect and remove PII via PIIDetector
     - Remove tracked changes/revisions
     - Remove comments
     - Strip core properties (author, last_modified_by, title, subject, keywords, etc.)
     - Save cleaned DOCX
     - Return stats: `{ sections_processed, pii_items_removed, metadata_stripped: True }`
   - Preserve RTL paragraph direction and formatting

2. Write tests in `tests/test_docx_cleaner.py`:
   ```python
   def create_test_docx(path, paragraphs):
       doc = Document()
       doc.core_properties.author = "John Smith"
       for text in paragraphs:
           doc.add_paragraph(text)
       doc.save(path)

   def test_removes_pii_from_paragraphs():
       create_test_docx(input_path, ["Contact John Smith", "Email: john@example.com"])
       cleaner.clean(input_path, output_path)
       doc = Document(output_path)
       full_text = " ".join(p.text for p in doc.paragraphs)
       assert "John" not in full_text
       assert "john@example.com" not in full_text

   def test_removes_pii_from_tables():
       # Create DOCX with table containing PII
       ...

   def test_strips_metadata():
       create_test_docx(input_path, ["Hello"])
       cleaner.clean(input_path, output_path)
       doc = Document(output_path)
       assert doc.core_properties.author in (None, "", "Unknown")

   def test_hebrew_docx():
       create_test_docx(input_path, ["שלום דוד כהן מתל אביב"])
       cleaner.clean(input_path, output_path)
       doc = Document(output_path)
       text = doc.paragraphs[0].text
       assert "דוד" not in text
   ```

3. Run: `pytest tests/test_docx_cleaner.py -v` → Expect: FAIL
4. Implement `DOCXCleaner`
5. Run: `pytest tests/test_docx_cleaner.py -v` → Expect: PASS

**Verification:** `pytest tests/test_docx_cleaner.py -v`
**Acceptance criteria:**
- [ ] PII removed from paragraphs, tables, headers, footers
- [ ] Tracked changes and comments removed
- [ ] Metadata stripped
- [ ] RTL Hebrew formatting preserved
- [ ] Output is a valid DOCX

---

### Task 5: Metadata Stripper

**Independent:** Yes
**Estimated scope:** Small (2 files)

**Files:**
- Create: `processor/metadata.py`
- Create: `tests/test_metadata.py` (can be part of pdf/docx tests)

**Steps:**
1. Write `processor/metadata.py`:
   - Function `strip_pdf_metadata(doc) -> None` — clears all PDF metadata fields
   - Function `strip_docx_metadata(doc) -> None` — clears all DOCX core properties
   - Both functions are called by the respective cleaners but isolated for clarity

2. Ensure metadata stripping is thorough:
   - PDF: author, title, subject, keywords, creator, producer, creation_date, mod_date, trapped
   - DOCX: author, last_modified_by, title, subject, keywords, category, comments, revision, created, modified

**Verification:** Covered by Task 3 and Task 4 tests
**Acceptance criteria:**
- [ ] All metadata fields cleared for both PDF and DOCX

---

### Task 6: Secure File Deletion

**Independent:** Yes
**Estimated scope:** Small (2 files)

**Files:**
- Create: `utils/secure_delete.py`
- Create: `tests/test_secure_delete.py`

**Steps:**
1. Write `utils/secure_delete.py`:
   - Function `secure_delete(file_path: str) -> None`:
     - Overwrite file contents with random bytes (same size)
     - Overwrite again with zeros
     - Call `os.unlink()` to delete
     - Log deletion (without logging file content or name — just "file deleted")
   - Function `secure_delete_dir(dir_path: str) -> None`:
     - Securely delete all files in directory, then remove directory

2. Write tests:
   ```python
   def test_file_deleted():
       path = create_temp_file("sensitive data")
       secure_delete(path)
       assert not os.path.exists(path)

   def test_content_overwritten_before_delete():
       # Verify file content is overwritten (check with a mock)
       ...
   ```

3. Run: `pytest tests/test_secure_delete.py -v` → Expect: PASS

**Verification:** `pytest tests/test_secure_delete.py -v`
**Acceptance criteria:**
- [ ] Files are overwritten before deletion
- [ ] Files are fully removed from disk
- [ ] No sensitive data in logs

---

### Task 7: Processing Pipeline (Orchestrator)

**Independent:** No — depends on Tasks 2, 3, 4, 5, 6
**Estimated scope:** Medium (2 files)

**Files:**
- Create: `processor/pipeline.py`
- Create: `tests/test_pipeline.py`

**Steps:**
1. Write `processor/pipeline.py`:
   - Class `CleaningPipeline`:
     - Init: creates `PIIDetector`, `PDFCleaner`, `DOCXCleaner` instances
     - Method `process(input_path: str) -> str`:
       - Detect file type by extension (.pdf or .docx)
       - Create temp output path
       - Route to appropriate cleaner
       - Return path to cleaned file
     - Method `cleanup(file_paths: list[str]) -> None`:
       - Securely delete all given file paths
   - Timeout handling via `signal.alarm` or `threading.Timer`

2. Write end-to-end tests:
   ```python
   def test_pipeline_pdf():
       cleaned = pipeline.process("tests/fixtures/sample_pii.pdf")
       assert os.path.exists(cleaned)
       # Verify no PII in output
       ...

   def test_pipeline_docx():
       cleaned = pipeline.process("tests/fixtures/sample_pii.docx")
       assert os.path.exists(cleaned)

   def test_pipeline_unsupported_type():
       with pytest.raises(ValueError, match="Unsupported"):
           pipeline.process("file.xlsx")

   def test_pipeline_cleanup():
       cleaned = pipeline.process(input_path)
       pipeline.cleanup([input_path, cleaned])
       assert not os.path.exists(input_path)
       assert not os.path.exists(cleaned)
   ```

3. Run: `pytest tests/test_pipeline.py -v` → Expect: PASS

**Verification:** `pytest tests/test_pipeline.py -v`
**Acceptance criteria:**
- [ ] PDF and DOCX files processed end-to-end
- [ ] PII removed and metadata stripped in output
- [ ] Unsupported file types rejected with clear error
- [ ] Cleanup deletes all temp files securely
- [ ] Processing times out after configured limit

---

### Task 8: Signal Bot Integration

**Independent:** No — depends on Task 7 (Pipeline)
**Estimated scope:** Medium (2-3 files)

**Files:**
- Create: `signal_bot.py`
- Create: `main.py`
- Create: `tests/test_signal_bot.py`

**Steps:**
1. Write `signal_bot.py`:
   - Class `SignalBot`:
     - Init: connect to signal-cli REST API
     - Method `start()` — start listening for incoming messages
     - Method `handle_message(message)`:
       - If message has attachment (PDF/DOCX):
         1. Send acknowledgment: "Processing your document..."
         2. Download attachment to temp dir
         3. Validate file size (< MAX_FILE_SIZE_MB)
         4. Run through `CleaningPipeline.process()`
         5. Send cleaned file back as attachment
         6. Send confirmation: "Done. All files deleted from server."
         7. Securely delete original + cleaned files
       - If message has unsupported attachment:
         - Reply: "Supported formats: PDF, DOCX"
       - If text-only message:
         - Reply with usage instructions
     - Error handling:
       - Processing failure: reply with error, still delete original
       - Timeout: reply with timeout message, clean up
       - File too large: reply with size limit message

2. Write `main.py`:
   ```python
   from signal_bot import SignalBot
   from config import Config

   def main():
       bot = SignalBot(Config)
       print(f"Clean-Data bot started on {Config.SIGNAL_PHONE_NUMBER}")
       bot.start()

   if __name__ == "__main__":
       main()
   ```

3. Write tests (mock signal-cli interactions):
   ```python
   def test_handles_pdf_attachment():
       # Mock incoming message with PDF attachment
       # Verify pipeline is called and response sent

   def test_handles_unsupported_file():
       # Mock incoming message with .xlsx
       # Verify error response sent

   def test_handles_text_message():
       # Mock text-only message
       # Verify usage instructions sent

   def test_handles_oversized_file():
       # Mock file > MAX_FILE_SIZE_MB
       # Verify rejection message sent

   def test_cleanup_on_error():
       # Mock processing failure
       # Verify original file still deleted
   ```

4. Run: `pytest tests/test_signal_bot.py -v` → Expect: PASS

**Verification:** `pytest tests/test_signal_bot.py -v`
**Acceptance criteria:**
- [ ] Bot receives and processes PDF/DOCX attachments via Signal
- [ ] Cleaned document sent back to user
- [ ] Appropriate error messages for unsupported files, oversized files, text-only messages
- [ ] All files securely deleted after processing (success or failure)
- [ ] Acknowledgment message sent before processing starts

---

## Dependency Graph

```
Task 1 (scaffolding)  ──────────────────────────────┐
Task 2 (PII detector)  ──┬──► Task 3 (PDF cleaner)  ├──► Task 7 (pipeline) ──► Task 8 (Signal bot)
                         ├──► Task 4 (DOCX cleaner) ─┤
Task 5 (metadata)  ──────┤                           │
Task 6 (secure delete)  ─┘                           │
                                                     │
```

**Parallelizable:** Tasks 1, 2, 5, 6
**Sequential:** Tasks 3 & 4 (after 2), Task 7 (after 3, 4, 5, 6), Task 8 (after 7)

---

## Verification Summary

| Task | Verification Command                        | Expected Output |
|------|---------------------------------------------|-----------------|
| 1    | `python -c "from config import Config; print('OK')"` | `OK` |
| 2    | `pytest tests/test_pii_detector.py -v`      | All tests pass  |
| 3    | `pytest tests/test_pdf_cleaner.py -v`       | All tests pass  |
| 4    | `pytest tests/test_docx_cleaner.py -v`      | All tests pass  |
| 5    | (covered by Tasks 3 & 4)                    | All tests pass  |
| 6    | `pytest tests/test_secure_delete.py -v`     | All tests pass  |
| 7    | `pytest tests/test_pipeline.py -v`          | All tests pass  |
| 8    | `pytest tests/test_signal_bot.py -v`        | All tests pass  |
| All  | `pytest tests/ -v`                          | All tests pass  |

---

## Setup Instructions (for deployment)

1. Install signal-cli and register a phone number
2. Start signal-cli REST API: `signal-cli-rest-api -u <number>`
3. Copy `.env.example` to `.env` and configure
4. Install dependencies: `pip install -r requirements.txt`
5. Download NLP models: `python -m spacy download en_core_web_lg`
6. Start the bot: `python main.py`
