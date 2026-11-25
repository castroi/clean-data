# Clean-Data

A privacy-first document sanitization tool. Users send PDF or DOCX files via a Signal bot, the system detects and removes all PII using Microsoft Presidio with English NLP and Israeli PII pattern recognizers, then returns the cleaned document. All files are securely deleted after processing. No data is retained and no external API calls are made.

## Features

- Receives PDF and DOCX files via Signal messenger
- **Automatic PII removal**: Detects and removes names, emails, phone numbers, Israeli IDs (Teudat Zehut), addresses
- **Custom word removal**: Use `/replace word1, word2, ...` to specify custom words/names/numbers to remove instead
- English-language document support with Israeli PII pattern detection (Teudat Zehut IDs, 05X/+972 phone numbers)
- Strips all document metadata (author, dates, creator, etc.)
- Securely deletes all files after processing (overwrite + unlink)
- Zero data retention policy
- All NLP models run locally — documents never leave the machine

## Stack

| Component | Purpose |
|---|---|
| Python 3.11+ | Runtime |
| Microsoft Presidio | PII detection (runs locally) |
| spaCy en_core_web_sm | English NLP |
| PyMuPDF | PDF processing |
| python-docx | DOCX processing |
| signal-cli REST API via [signal-wrapper](https://github.com/castroi/signal-wrapper) | Signal bot integration |
| pytest | Testing |

## Architecture

```
main.py → SignalBot → CleaningPipeline → PDFCleaner / DOCXCleaner → PIIDetector
             │                         → secure_delete (cleanup)
             └→ SignalClient → wrapper-clean-data → shared signal-cli
```

### Key Modules

| Module | Description |
|---|---|
| `signal_bot.py` | Signal bot with `/replace` and `/end` commands, rate limiting, sender allowlist |
| `word_session_store.py` | Per-sender custom word storage with 1-hour TTL for `/replace` command |
| `processor/pipeline.py` | Orchestrates cleaning, enforces timeout |
| `processor/pii_detector.py` | Presidio + spaCy English NER with Israeli regex recognizers |
| `processor/custom_word_detector.py` | Case-insensitive text matching for user-specified custom words |
| `processor/pdf_cleaner.py` | PDF text cleaning + metadata stripping |
| `processor/docx_cleaner.py` | DOCX cleaning (paragraphs, tables, headers/footers) + metadata |
| `processor/metadata.py` | PDF and DOCX metadata stripping |
| `utils/secure_delete.py` | Secure file overwrite and deletion |
| `config.py` | Environment variable configuration |

## Setup

### 1. Clone the repository

```bash
git clone <repo-url>
cd clean-data
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
TMPDIR=~/tmp pip install --no-cache-dir -r requirements.txt
```

### 4. Download the spaCy English model

```bash
python3 -m spacy download en_core_web_sm
```

### 5. Configure Signal access

The bot talks to Signal only through its own [signal-wrapper](https://github.com/castroi/signal-wrapper), which sits in front of a [signal-cli REST API](https://github.com/bbernhard/signal-cli-rest-api) container running `MODE=json-rpc` with the bot's number registered. The wrapper must run with `ALLOW_ATTACHMENTS=true` and a token. Point `SIGNAL_API_URL` at the wrapper and set `SIGNAL_TOKEN` (or `SIGNAL_TOKEN_FILE`) to the wrapper's token.

### 6. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` with your values (see Configuration below).

### 7. Run

```bash
python3 main.py
```

## Docker Deployment

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/)

### 1. Configure environment

```bash
cp .env.example .env
```

Edit `.env`: set `SIGNAL_PHONE_NUMBER` to the bot's number, `SIGNAL_BACKEND_URL` to the shared signal-cli on the `signal-net` network, and `ALLOWED_SENDERS` to the Signal UUIDs allowed to use the bot (required; `.env.example` explains how to find a UUID). `SIGNAL_API_URL`, `SIGNAL_TOKEN_FILE`, `TEMP_DIR` and the send limit are fixed in `docker-compose.yml`, which passes only the variables it lists into the containers.

Create the token shared by the bot and its wrapper (never put it in `.env`):

```bash
mkdir -p secrets && openssl rand -hex 32 > secrets/signal_token && sudo chgrp 10001 secrets/signal_token && chmod 640 secrets/signal_token
```

### 2. Register your Signal number (one-time)

Register the number on the **shared** signal-cli container (`MODE=json-rpc`), not through the wrapper, which blocks registration by design. See **"Set up a phone number"** below. The number must match `SIGNAL_PHONE_NUMBER`.

### 3. Start

```bash
docker compose up -d
```

### 4. View logs

View the bot logs to monitor processing and troubleshoot errors:

```bash
docker logs clean-data-clean-data-1
```

View the wrapper logs to check send/receive and denied requests (use `--tail` to limit output):
```bash
docker logs clean-data-wrapper-clean-data-1 --tail 10
```

### 5. Stop

```bash
docker compose down
```

### Docker Notes

- **signal-cli** is shared and runs outside this project. Its registration and data are managed there.
- **Received files** are deleted from signal-cli's storage through the wrapper (`DELETE /v1/attachments/{id}`) after every message, whether it was processed or rejected.
- **Temp files** are stored on a `tmpfs` mount and are lost on restart — this is by design for maximum PII protection.
- **NLP models** are baked into the Docker image at build time. No internet access is needed at runtime.
- No ports are published. clean-data reaches only its wrapper; only the wrapper is on `signal-net`.

## Configuration

All configuration is done via environment variables or a `.env` file.

| Variable | Description | Default |
|---|---|---|
| `SIGNAL_PHONE_NUMBER` | Bot's registered Signal phone number | required |
| `SIGNAL_API_URL` | This bot's signal-wrapper URL | `http://localhost:8080` |
| `SIGNAL_TOKEN_FILE` | File holding the wrapper bearer token (Docker secret) | (unset) |
| `SIGNAL_TOKEN` | Token fallback when no file is set (local/testing) | (unset) |
| `SIGNAL_BACKEND_URL` | Shared signal-cli URL, used by the wrapper (compose only) | `http://signal-api:8080` |
| `TEMP_DIR` | Temporary file directory | `/tmp/clean-data` |
| `MAX_FILE_SIZE_MB` | Maximum accepted file size in MB | `25` |
| `PROCESSING_TIMEOUT` | Maximum processing time in seconds | `300` |
| `ALLOWED_SENDERS` | **Required.** Comma-separated allowlist of Signal UUIDs (not phone numbers). Missing, empty or any invalid entry stops startup | — |
| `SIGNAL_MAX_SEND_BYTES` | Largest send body the wrapper accepts (its `MAX_BODY_BYTES`); bigger cleaned files are not sent and the user is told | `40000000` |
| `HEARTBEAT_FILE` | File touched on every frame/pong from the receive stream; used by the Docker healthcheck | (unset) |
| `WORD_SESSION_TTL_SECONDS` | TTL for `/replace` custom word sessions | `3600` (1 hour) |
| `MAX_WORDS_PER_SENDER` | Maximum custom words per `/replace` session | `15` |
| `MAX_WORD_LENGTH` | Maximum length per custom word | `100` |
| `MAX_WORD_SESSIONS` | Maximum concurrent `/replace` sessions | `100` |

For strongest PII protection, mount `TEMP_DIR` on a `tmpfs` volume so temporary files never touch disk.

## Usage

### Automatic PII Removal (Default)

Send a PDF or DOCX file to the bot. It will automatically detect and remove all PII (names, emails, phone numbers, IDs, addresses) and return the cleaned document.

### Custom Word Removal (`/replace` command)

Specify custom words to remove instead of automatic PII detection:

```
User: /replace Amit, Joni, David Cohen
Bot:  3 word(s) added (3 total active).
      These words will be removed from documents sent in the next hour.
      Send /end to clear.
```

Then send documents — all occurrences of those words will be removed (case-insensitive).

**Features:**
- Words accumulate: send `/replace Amit` then `/replace Joni` and both will be removed
- Supports multi-word phrases: `/replace David Cohen, רחוב הרצל` (English and Hebrew)
- Case-insensitive matching: `/replace amit` removes "Amit", "AMIT", "amit"
- 1-hour expiration: words are automatically cleared after 1 hour of inactivity
- Per-sender isolation: each user's custom words don't affect others

**Clear custom words:**
```
User: /end
Bot:  Custom words cleared. Back to automatic PII detection.
```

## Docker Architecture

```
            signal-net (shared)                     cleandatanet (private)
┌──────────────────────────┐   ┌─────────────────────┐   ┌─────────────────────────┐
│  shared signal-cli       │◄──│  wrapper-clean-data │◄──│  clean-data container   │
│  bbernhard/              │   │  castroi/           │   │  (Python bot + NLP)     │
│  signal-cli-rest-api     │   │  signal-wrapper     │   │  Presidio, spaCy,       │
│  MODE=json-rpc           │   │  pins sender,       │   │  PyMuPDF, python-docx   │
│  (separate project)      │   │  bearer token       │   │                         │
└──────────────────────────┘   └─────────────────────┘   └─────────────────────────┘
                                                                     │
                                                                     ▼
                                                          tmpfs /tmp/clean-data
                                                          (PII files, never on disk)
```

- **shared signal-cli** handles the Signal protocol for all bots. It is not part of this compose file.
- **wrapper-clean-data** is this bot's own [signal-wrapper](https://github.com/castroi/signal-wrapper). It forces every send to come from `SIGNAL_PHONE_NUMBER`, pins the receive WebSocket to it, requires the bearer token, and exposes only send, receive and (with `ALLOW_ATTACHMENTS=true`) single-attachment download/delete.
- **clean-data** is built from the project's `Dockerfile`, with all NLP models baked in. It receives messages over the wrapper's WebSocket, downloads each document, cleans it, sends the result back, and deletes the original from signal-cli.

Temporary files are stored on a `tmpfs` mount, so PII never touches persistent storage.

### Useful Docker Commands

Rebuild the clean-data image after code changes (does not affect the shared signal-cli or its registration):
```bash
docker compose build clean-data
```


## Running Tests

Activate the virtual environment before running tests:

```bash
source .venv/bin/activate
```

Run test files individually to avoid excessive memory usage from multiple concurrent NLP model instances:

```bash
pytest tests/test_pii_detector.py -v
pytest tests/test_pdf_cleaner.py -v
pytest tests/test_docx_cleaner.py -v
pytest tests/test_metadata.py -v
pytest tests/test_secure_delete.py -v
pytest tests/test_pipeline.py -v
pytest tests/test_signal_bot.py -v
```

## Security

- All processing is local — documents never leave the machine
- Files are securely overwritten (random bytes followed by zeros) before deletion
- Rate limiting: 5 files per 60 seconds per sender
- Required sender allowlist of Signal UUIDs: identity is the sender's `sourceUuid` only, unknown senders are dropped silently (no reply, no id logged)
- Filename sanitization prevents path traversal attacks
- Stale temporary files are purged on startup
- No PII is written to logs — exception details are logged at DEBUG level only
- End-to-end encrypted messaging via Signal

## Privacy Principles

- **Zero data retention** — all files are deleted immediately after processing, including the copy signal-cli keeps of each received attachment (deleted right after download, and every other attachment in a message — previews, quote thumbnails, edits, stories — when the message is done). Caveats: a hard crash or OOM kill between download and delete can leave the original in signal-cli, and files sent to the shared signal-cli's other (text-only) bots are not deleted by anyone (castroi/signal-wrapper#23).
- **No external API calls** — all NLP models run locally on CPU
- **No PII in logs** — exception details are logged at DEBUG level only
- **End-to-end encrypted** — all communication via Signal

## Known Limitations

- Scanned and image-based PDFs are not supported (no OCR)
- PDF annotations, form fields, and embedded files are not yet cleaned
- DOCX footnotes, tracked changes, and text boxes are not yet cleaned
- Secure delete is not effective on copy-on-write or SSD filesystems without tmpfs or full-disk encryption


## Set up a phone number
Run these against the **shared signal-cli container directly** (for example from a container on `signal-net`, or a temporarily published port), never through the wrapper, which blocks `register` by design.

You will need to set up the phone number(s) to send message.In order to do so, make sure, you have `curl` installed on your system. You can then issue the commands shown below from the command line. We use the example server `127.0.0.1` on port `8080`. If you have set up a different server, you will have to change this in the commands.

### Register a new phone number
1. In order to send signal messages to other users, you first need to register your phone number. This can be done via REST requests with:

   **Note**: If you want to register a land-line number, set the `use_voice` parameter to `true`. Signal will then call you on your number and speak the token to you instead of sending an SMS.

   ```sh
   curl -X POST -H "Content-Type: application/json" --data '{"use_voice": false}' 'http://<ip>:<port>/v1/register/<number>'
   ```

   **Example**: The following command registers the number `+9720521111111` to the Signal network.

   ```sh
   curl -X POST -H "Content-Type: application/json" --data '{"use_voice": false}' 'http://127.0.0.1:8080/v1/register/+9720521111111'
   ```

2. After you've sent the registration request, you will receive a token via SMS (or it will be spoken to you) for verfication.

3. In order to complete the registration process, you need to send the verification token back via the following REST request:

   ```sh
   curl -X POST -H "Content-Type: application/json" 'http://<ip>:<port>/v1/register/<number>/verify/<verification code>'
   ```

   **Example**: The following will send a verification code for the previous example number.

   ```sh
   curl -X POST -H "Content-Type: application/json" 'http://127.0.0.1:8080/v1/register/+9720521111111/verify/123456'
   ```


### Troubleshooting: CAPTCHA Required
If you receive a response like `{“error”:”Captcha required for verification (null)\n”}`, Signal requires a CAPTCHA to complete registration. Follow these steps:

1. Open [https://signalcaptchas.org/registration/generate.html](https://signalcaptchas.org/registration/generate.html) in your default browser
2. Complete the CAPTCHA challenge
3. Right-click the “Open Signal” button and select “Copy link address” to extract the CAPTCHA token
4. Use the extracted CAPTCHA value in your registration request:

```sh
curl -X POST -H “Content-Type: application/json” -d '{“captcha”:”<captcha value>”, “use_voice”: false}' 'http://127.0.0.1:8080/v1/register/<number>'
```


## License

TODO: Add license


