# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Clean-Data is a privacy-first document sanitization service. It receives PDF/DOCX files via a Signal bot, removes all PII (names, IDs, addresses, emails, phone numbers, metadata), and returns the cleaned document. Zero data retention — files are securely deleted after processing.

## Stack

- **Language:** Python 3.11+
- **PII Detection:** Microsoft Presidio (analyzer + anonymizer)
- **English NER:** spaCy `en_core_web_sm` (falls back from `en_core_web_lg`)
- **PDF Processing:** PyMuPDF (fitz)
- **DOCX Processing:** python-docx
- **Messaging:** Signal via signal-cli REST API (`pysignalclirestapi`)
- **Config:** python-dotenv (`.env` file)
- **Tests:** pytest

## Commands

```bash
# Activate virtual environment (required before all commands)
source .venv/bin/activate

# Install dependencies (use TMPDIR override if /tmp is small)
TMPDIR=~/tmp pip install --no-cache-dir -r requirements.txt

# Download NLP models (required once)
python3 -m spacy download en_core_web_sm

# Run all tests (may OOM if run together — run per-file instead)
pytest tests/ -v

# Run a single test file
pytest tests/test_pii_detector.py -v

# Run a specific test
pytest tests/test_pii_detector.py::test_removes_english_name -v

# Start the bot
python3 main.py
```

Note: The full test suite loads multiple PIIDetector instances (NLP models) which can exceed 1GB RAM. Run test files individually if memory-constrained.

## Docker Deployment

The project includes a `Dockerfile` and `docker-compose.yml` for containerized deployment:

```bash
# Copy and configure environment
cp .env.example .env

# Edit .env and set SIGNAL_PHONE_NUMBER to the phone you will register

# Start services
docker compose up -d

# Register Signal number using curl POST requests
# See "Set up a phone number" section in README.md for detailed instructions
```

**Architecture:** Two containers run on an internal bridge network:
- **signal-cli** — bbernhard/signal-cli-rest-api image. Handles Signal protocol, message I/O, attachment storage. Credentials persist in `signal-data` Docker volume.
- **clean-data** — Custom Python image with all NLP models baked in. Communicates with signal-cli over internal network. Temp files stored on tmpfs mount (PII never touches disk).

For complete Signal registration and CAPTCHA troubleshooting instructions, see the "Set up a phone number" section in README.md.

## Architecture

```
main.py → SignalBot → CleaningPipeline → PDFCleaner / DOCXCleaner → PIIDetector
                                       → secure_delete (cleanup)
```

- **`signal_bot.py`** — Signal bot listener. Receives file attachments, dispatches to pipeline, sends back cleaned files. All error paths must still delete the original file. Includes rate limiting, sender allowlist, filename sanitization, and startup temp file purge.
- **`processor/pipeline.py`** — Orchestrates cleaning. Routes by file extension, enforces timeout, calls secure cleanup.
- **`processor/pii_detector.py`** — Wraps Presidio with English NLP (spaCy `en_core_web_sm`). Includes custom recognizers for Israeli PII (Teudat Zehut, 05X phones, +972 format).
- **`processor/pdf_cleaner.py`** / **`processor/docx_cleaner.py`** — Format-specific cleaners. Extract text, run through PIIDetector, rebuild document, strip metadata.
- **`processor/metadata.py`** — Strips all document metadata fields (author, dates, creator, etc.).
- **`utils/secure_delete.py`** — Overwrites file contents with random bytes then zeros before unlinking. Used for both original and cleaned files after sending.
- **`config.py`** — Loads settings from `.env` with defaults.

## Key Design Constraints

- **All processing is local.** No external API calls. NLP models run on-device (CPU). Documents never leave the machine.
- **PII removal mode is full deletion** (not redaction or placeholders). Cleaned text has PII stripped entirely.
- **Zero data retention.** Both original and cleaned files are securely deleted immediately after the cleaned version is sent back via Signal. No temp files should survive a completed or failed processing run.
- **Signal bot error paths must always clean up.** If processing fails or times out, the original file must still be securely deleted before responding with an error.

## Security Rules

Security rules enforced across the codebase:

- **Never log PII.** Exception messages may contain document content — log exception type at ERROR, detail at DEBUG only.
- **Sanitize all user-supplied filenames.** Strip path components, validate the resolved path stays inside TEMP_DIR to prevent path traversal.
- **Validate actual content size**, not just metadata `size` field from Signal API.
- **Use `secrets.token_hex()`** for temp filenames, never predictable values like `time.time()`.
- **Rate limit per sender** (5 files / 60 seconds). Configurable allowlist via `ALLOWED_SENDERS`.
- **Purge stale temp files on startup** in case previous process was killed.
- **Known limitations** (documented in audit): secure delete is not effective on CoW/SSD filesystems — recommend mounting TEMP_DIR on tmpfs. PDF/DOCX cleaners don't yet cover annotations, form fields, footnotes, tracked changes, or embedded files.

## Configuration

All config via `.env` (see `.env.example`). Key vars: `SIGNAL_PHONE_NUMBER`, `SIGNAL_CLI_URL`, `TEMP_DIR`, `MAX_FILE_SIZE_MB`, `PROCESSING_TIMEOUT`, `ALLOWED_SENDERS` (optional, comma-separated phone numbers).
