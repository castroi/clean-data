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
- **Messaging:** Signal via this bot's own [signal-wrapper](https://github.com/castroi/signal-wrapper) in front of a shared signal-cli REST API (`MODE=json-rpc`). Transport in `signal_client.py` (`requests` + `websocket-client`)
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

CI (`.github/workflows/ci.yml`, job `check`, required by the `main` ruleset) installs `requirements.txt` + the spaCy model on Python 3.11, runs `pip-audit`, runs every test file separately, and checks `docker compose config`. Actions are pinned to commit SHAs; Dependabot (`.github/dependabot.yml`) proposes pip and action updates weekly after a 7-day cooldown.

Note: The full test suite loads multiple PIIDetector instances (NLP models) which can exceed 1GB RAM. Run test files individually if memory-constrained.

## Docker Deployment

The project includes a `Dockerfile` and `docker-compose.yml` for containerized deployment:

```bash
# Copy and configure environment
cp .env.example .env

# Edit .env: SIGNAL_PHONE_NUMBER, SIGNAL_BACKEND_URL (shared signal-cli on signal-net)
# and ALLOWED_SENDERS (required, Signal UUIDs)
# Create the wrapper token (Docker secret, never in .env)
mkdir -p secrets && openssl rand -hex 32 > secrets/signal_token && sudo chgrp 10001 secrets/signal_token && chmod 640 secrets/signal_token

# Start services
docker compose up -d

# Register the number on the SHARED signal-cli directly (the wrapper blocks register)
# See "Set up a phone number" section in README.md for detailed instructions
```

**Architecture:** Two containers in this project, plus a shared signal-cli outside it:
- **wrapper-clean-data** — `ghcr.io/castroi/signal-wrapper`. Pins sender/receive to `SIGNAL_PHONE_NUMBER`, requires the bearer token (Docker secret `secrets/signal_token`), runs with `ALLOW_ATTACHMENTS=true`. Only service on the external `signal-net`.
- **clean-data** — Custom Python image with all NLP models baked in. Reaches only its wrapper over a private bridge. Temp files stored on tmpfs mount (PII never touches disk).
- **shared signal-cli** — bbernhard/signal-cli-rest-api, `MODE=json-rpc`, managed separately.

For complete Signal registration and CAPTCHA troubleshooting instructions, see the "Set up a phone number" section in README.md.

## Architecture

```
main.py → SignalBot → CleaningPipeline → PDFCleaner / DOCXCleaner → PIIDetector
             │                         → secure_delete (cleanup)
             └→ SignalClient → wrapper-clean-data → shared signal-cli
```

- **`signal_client.py`** — All Signal transport: receive WebSocket (reconnect with backoff, json-rpc frames), `/v2/send` with bearer token and no `number`, attachment download (size-capped) and delete. `session`/`ws_connect` injectable for tests.
- **`signal_bot.py`** — Signal bot listener. Receives file attachments, dispatches to pipeline, sends back cleaned files. All error paths must still delete the original file. Includes rate limiting, sender allowlist, filename sanitization, and startup temp file purge. Custom words: plain text (no commands) adds words, `-word` removes one, whole-message control words `0`/`list`/`help` (EN+HE); stored per sender in memory by `word_session_store.py` (sliding TTL). Custom words are always **added to** automatic PII removal (`CustomWordDetector(words, base=pipeline.detector)`), never a replacement. The per-file summary is a separate message, never the file's caption.
- **`processor/pipeline.py`** — Orchestrates cleaning. Routes by file extension, enforces timeout, calls secure cleanup.
- **`processor/pii_detector.py`** — Wraps Presidio with English NLP (spaCy `en_core_web_sm`). Includes custom recognizers for Israeli PII (Teudat Zehut, 05X phones, +972 format).
- **`processor/pdf_cleaner.py`** / **`processor/docx_cleaner.py`** — Format-specific cleaners. Extract text, run through PIIDetector, rebuild document, strip metadata.
- **`processor/metadata.py`** — Strips all document metadata fields (author, dates, creator, etc.).
- **`utils/secure_delete.py`** — Overwrites file contents with random bytes then zeros before unlinking. Used for both original and cleaned files after sending.
- **`shutdown.py`** — SIGTERM handling: SystemExit now, or postponed while inside `deferred()` (delete sweeps); shutdown time budget.
- **`config.py`** — Loads settings from `.env` with defaults.

## Key Design Constraints

- **All processing is local.** No external API calls. NLP models run on-device (CPU). Documents never leave the machine.
- **PII removal mode is full deletion** (not redaction or placeholders). Cleaned text has PII stripped entirely.
- **Zero data retention.** Both original and cleaned files are securely deleted immediately after the cleaned version is sent back via Signal. No temp files should survive a completed or failed processing run.
- **Signal bot error paths must always clean up.** If processing fails or times out, the original file must still be securely deleted before responding with an error.
- **Delete received attachments from signal-cli.** A document's original is deleted via the wrapper right after it is downloaded (with retries), before processing. `on_receive` then deletes every remaining attachment id found anywhere in the frame (`collect_attachment_ids`: edit/sync/story messages, previews, quote thumbnails, avatars), on every path. The "deleted" text is only sent when the delete succeeded. SIGTERM becomes SystemExit so these `finally` blocks run on `docker stop` (`shutdown.py`): inside a delete sweep the exit is postponed until the sweep ends (`shutdown.deferred()`), deletes after SIGTERM (checked per attempt, so also one already running) are single 5 s attempts within a 20 s budget (< `stop_grace_period` 30 s; normal deletes time out after 15 s), and a repeat SIGTERM or Ctrl-C is ignored. On shutdown `receive_forever` closes the stream (no new frames are taken from signal-cli), waits for the reader while draining the queue, and hands every frame it took but did not fully handle (queued ones and the in-flight one) to `SignalBot.on_shutdown`, which deletes their attachments without processing them (signal-cli does not redeliver) and logs the counts, including any left behind. Remaining gaps: hard kill/OOM; a microsecond window inside `Queue.get`; a SIGTERM seen late because a long C call (PyMuPDF/spaCy) was running.
- **Temp names carry no user data.** `input_<token_hex><ext>`; the user's filename never touches disk or logs.
- **Receive on a thread, process on the main thread.** `SignalClient.receive_forever` reads the WebSocket (pings, reconnects) on a background thread and queues frames; processing stays on the main thread because the pipeline timeout (SIGALRM) and shutdown (SIGTERM) need it.

## Security Rules

Security rules enforced across the codebase:

- **Never log PII.** Exception messages may contain document content — log exception type at ERROR, detail at DEBUG only.
- **Sanitize all user-supplied filenames.** Strip path components, validate the resolved path stays inside TEMP_DIR to prevent path traversal.
- **Validate actual content size**, not just metadata `size` field from Signal API.
- **Use `secrets.token_hex()`** for temp filenames, never predictable values like `time.time()`.
- **Rate limit per sender** (5 files / 60 seconds, 20 messages / 60 seconds). **Required** allowlist via `ALLOWED_SENDERS` (Signal UUIDs, as in ha-signal-remote): missing, empty or any invalid entry stops startup. Identity is the `sourceUuid` only (no phone-number fallback); the allowlist is checked first; unknown senders are dropped silently and only a rate-limited count is logged, never an id or message.
- **Purge stale temp files on startup** in case previous process was killed.
- **Known limitations** (documented in audit): secure delete is not effective on CoW/SSD filesystems — recommend mounting TEMP_DIR on tmpfs. PDF/DOCX cleaners don't yet cover annotations, form fields, footnotes, tracked changes, or embedded files.

## Configuration

All config via `.env` (see `.env.example`). Under Docker, `docker-compose.yml` passes only an explicit list of variables (no `env_file`); required ones use `${VAR:?}` so `docker compose up` fails fast. Key vars: `SIGNAL_PHONE_NUMBER`, `SIGNAL_API_URL` (the wrapper), `SIGNAL_TOKEN_FILE` / `SIGNAL_TOKEN`, `SIGNAL_BACKEND_URL` (compose, for the wrapper), `TEMP_DIR`, `MAX_FILE_SIZE_MB`, `PROCESSING_TIMEOUT`, `ALLOWED_SENDERS` (**required**, comma-separated Signal UUIDs — not phone numbers; missing or invalid stops startup), `SIGNAL_MAX_SEND_BYTES` (= the wrapper's `MAX_BODY_BYTES`), `HEARTBEAT_FILE` (healthcheck).
