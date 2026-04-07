# Clean-Data

A privacy-first document sanitization tool. Users send PDF or DOCX files via a Signal bot, the system detects and removes all PII using Microsoft Presidio and a local Hebrew NER model, then returns the cleaned document. All files are securely deleted after processing. No data is retained and no external API calls are made.

## Features

- Receives PDF and DOCX files via Signal messenger
- Detects and removes PII: names, emails, phone numbers, Israeli IDs (Teudat Zehut), addresses
- Supports English and Hebrew (RTL) documents, including bilingual content
- Strips all document metadata (author, dates, creator, etc.)
- Securely deletes all files after processing (overwrite + unlink)
- Zero data retention policy
- All NLP models run locally — documents never leave the machine

## Stack

| Component | Purpose |
|---|---|
| Python 3.11+ | Runtime |
| Microsoft Presidio | PII detection (runs locally) |
| HuggingFace heBERT-NER | Hebrew named entity recognition (runs locally) |
| spaCy en_core_web_sm | English NLP |
| PyMuPDF | PDF processing |
| python-docx | DOCX processing |
| signal-cli REST API | Signal bot integration |
| pytest | Testing |

## Architecture

```
main.py → SignalBot → CleaningPipeline → PDFCleaner / DOCXCleaner → PIIDetector
                                       → secure_delete (cleanup)
```

### Key Modules

| Module | Description |
|---|---|
| `signal_bot.py` | Signal bot with rate limiting, sender allowlist, filename sanitization |
| `processor/pipeline.py` | Orchestrates cleaning, enforces timeout |
| `processor/pii_detector.py` | Presidio + heBERT-NER dual-language PII detection |
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

### 5. Download the Hebrew NER model (optional)

The model downloads automatically on first use. To download it manually:

```bash
python3 -c "from transformers import pipeline; pipeline('ner', model='avichr/heBERT-NER')"
```

Note: this requires approximately 500MB of disk space.

### 6. Configure signal-cli

Install and configure the [signal-cli REST API](https://github.com/bbernhard/signal-cli-rest-api) with a registered phone number.

### 7. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` with your values (see Configuration below).

### 8. Run

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

Edit `.env` and set `SIGNAL_PHONE_NUMBER` to the phone number you will register. `SIGNAL_CLI_URL` and `TEMP_DIR` are set automatically by Docker Compose.

### 2. Register your Signal number (one-time)

Follow the detailed instructions in the **"Set up a phone number"** section below to register and verify your Signal number. The phone number must match the `SIGNAL_PHONE_NUMBER` in your `.env` file — the clean-data bot uses it to poll the signal-cli REST API for incoming messages.

### 3. Start

```bash
docker compose up -d
```

### 4. View logs

View the bot logs to monitor processing and troubleshoot errors:

```bash
docker logs clean-data-clean-data-1
```

View the signal-cli logs to check message delivery and API requests (use `--tail` to limit output):
```bash
docker logs clean-data-signal-cli-1 --tail 10
```

### 5. Stop

```bash
docker compose down
```

### Docker Notes

- **signal-cli data** persists in a Docker named volume (`signal-data`). Your registration survives container restarts.
- **Temp files** are stored on a `tmpfs` mount and are lost on restart — this is by design for maximum PII protection.
- **NLP models** are baked into the Docker image at build time. No internet access is needed at runtime.
- The signal-cli port is **not exposed to the host** — communication happens over an internal Docker network.

## Configuration

All configuration is done via environment variables or a `.env` file.

| Variable | Description | Default |
|---|---|---|
| `SIGNAL_PHONE_NUMBER` | Bot's registered Signal phone number | required |
| `SIGNAL_CLI_URL` | signal-cli REST API URL | `http://localhost:8080` |
| `TEMP_DIR` | Temporary file directory | `/tmp/clean-data` |
| `MAX_FILE_SIZE_MB` | Maximum accepted file size in MB | `25` |
| `PROCESSING_TIMEOUT` | Maximum processing time in seconds | `300` |
| `ALLOWED_SENDERS` | Comma-separated allowlist of Signal UUIDs | (all senders allowed) |

For strongest PII protection, mount `TEMP_DIR` on a `tmpfs` volume so temporary files never touch disk.

## Dockers Architecture

```
┌─────────────────────────┐       ┌─────────────────────────┐
│  signal-cli container   │       │  clean-data container   │
│  (REST API on :8080)    │◄──────│  (Python bot + NLP)     │
│                         │       │  Presidio, spaCy,       │
│  bbernhard/             │       │  heBERT, PyMuPDF        │
│  signal-cli-rest-api    │       │  custom Dockerfile      │
└─────────────────────────┘       └─────────────────────────┘
         │                                   │
         ▼                                   ▼
   signal-data volume               tmpfs /tmp/clean-data
   (Signal credentials)             (PII files, never on disk)
```

Two containers run on an internal Docker bridge network:

- **signal-cli** uses the official [bbernhard/signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) image. It handles Signal protocol, message sending/receiving, and attachment storage. Signal credentials are persisted in a named Docker volume.
- **clean-data** is built from the project's `Dockerfile`. It packages the Python application with all NLP models baked in (no internet needed at runtime). It communicates with signal-cli over the internal network via HTTP REST API.

The signal-cli port is **not exposed to the host** — only the clean-data container can reach it. Temporary files are stored on a `tmpfs` mount, so PII never touches persistent storage. This addresses the secure deletion concern from the [security audit](docs/cybersecurity.md).

### Useful Docker Commands

Rebuild the clean-data image after code changes (does not affect signal-cli or its registration):
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
- Optional sender allowlist restricts access to trusted Signal UUIDs
- Filename sanitization prevents path traversal attacks
- Stale temporary files are purged on startup
- No PII is written to logs — exception details are logged at DEBUG level only
- End-to-end encrypted messaging via Signal

A full security audit is available at [`docs/cybersecurity.md`](docs/cybersecurity.md).

## Privacy Principles

- **Zero data retention** — all files are deleted immediately after processing
- **No external API calls** — all NLP models run locally on CPU
- **No PII in logs** — exception details are logged at DEBUG level only
- **End-to-end encrypted** — all communication via Signal

## Known Limitations

- Scanned and image-based PDFs are not supported (no OCR)
- PDF annotations, form fields, and embedded files are not yet cleaned
- DOCX footnotes, tracked changes, and text boxes are not yet cleaned
- Secure delete is not effective on copy-on-write or SSD filesystems without tmpfs or full-disk encryption


## Set up a phone number
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


