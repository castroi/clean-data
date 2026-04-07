# Docker Setup Implementation Plan

**Goal:** Containerize the clean-data project with two Docker services — signal-cli (official image) and clean-data bot (custom image) — connected via Docker Compose with tmpfs for secure temp files.

**Architecture:** Two containers on an internal Docker bridge network. signal-cli handles Signal protocol; clean-data bot communicates with it via HTTP REST API. NLP models baked into the clean-data image. Temp files on tmpfs (never touch persistent storage).

**Key decisions:**
- Multi-stage Dockerfile to minimize image size (~2.5GB with CPU-only PyTorch + NLP models)
- NLP models baked into image at build time — no internet needed at runtime (privacy)
- tmpfs mount for TEMP_DIR — strongest PII protection per security audit
- Non-root user inside container
- signal-cli port not exposed to host — internal network only
- Named volume for signal-cli data persistence

---

## Tasks

### Task 1: Create .dockerignore

**Independent:** Yes
**Estimated scope:** Small (1 file)

**Files:**
- Create: `.dockerignore`

**Steps:**
1. Create `.dockerignore` to exclude files not needed in the Docker image:
   ```
   .venv/
   .env
   __pycache__/
   *.pyc
   .pytest_cache/
   .git/
   .gitignore
   tests/
   docs/
   *.md
   .mypy_cache/
   .ruff_cache/
   ```

**Verification:** `cat .dockerignore` — file exists with expected content
**Acceptance criteria:**
- [ ] .dockerignore excludes dev/test files
- [ ] .env is excluded (secrets must not be baked into image)
- [ ] tests/ and docs/ excluded from production image

---

### Task 2: Create Dockerfile

**Independent:** Yes
**Estimated scope:** Small (1 file)

**Files:**
- Create: `Dockerfile`

**Steps:**
1. Create multi-stage `Dockerfile`:

   **Stage 1 — builder:**
   ```dockerfile
   FROM python:3.11-slim AS builder

   RUN apt-get update && apt-get install -y --no-install-recommends \
       gcc g++ && rm -rf /var/lib/apt/lists/*

   WORKDIR /app

   COPY requirements.txt .

   RUN python -m venv /opt/venv
   ENV PATH="/opt/venv/bin:$PATH"

   RUN pip install --no-cache-dir -r requirements.txt

   # Download and cache NLP models
   RUN python -m spacy download en_core_web_sm
   RUN python -c "from transformers import AutoTokenizer, AutoModelForTokenClassification; \
       AutoTokenizer.from_pretrained('avichr/heBERT-NER'); \
       AutoModelForTokenClassification.from_pretrained('avichr/heBERT-NER')"
   ```

   **Stage 2 — runtime:**
   ```dockerfile
   FROM python:3.11-slim

   # Create non-root user
   RUN groupadd -r cleandata && useradd -r -g cleandata -m cleandata

   WORKDIR /app

   # Copy venv and model caches from builder
   COPY --from=builder /opt/venv /opt/venv
   COPY --from=builder /root/.cache/huggingface /home/cleandata/.cache/huggingface

   # Copy application source
   COPY config.py main.py signal_bot.py ./
   COPY processor/ ./processor/
   COPY utils/ ./utils/

   # Set environment
   ENV PATH="/opt/venv/bin:$PATH"
   ENV HF_HOME="/home/cleandata/.cache/huggingface"
   ENV TRANSFORMERS_CACHE="/home/cleandata/.cache/huggingface"
   ENV PYTHONUNBUFFERED=1

   # Fix ownership
   RUN chown -R cleandata:cleandata /app /home/cleandata

   USER cleandata

   ENTRYPOINT ["python3", "main.py"]
   ```

2. Run: `docker build -t clean-data .` → Expect: successful build
3. Run: `docker run --rm clean-data python3 -c "from config import Config; print('OK')"` → Expect: `OK`

**Verification:** `docker build -t clean-data . && docker run --rm clean-data python3 -c "import fitz, docx, presidio_analyzer; print('OK')"`
**Acceptance criteria:**
- [ ] Image builds successfully
- [ ] All Python imports work inside container
- [ ] NLP models are pre-loaded (no download at runtime)
- [ ] Runs as non-root user (cleandata)
- [ ] No .env or test files in the image

---

### Task 3: Create docker-compose.yml

**Independent:** No — depends on Task 2 (Dockerfile must exist)
**Estimated scope:** Small (1 file)

**Files:**
- Create: `docker-compose.yml`

**Steps:**
1. Create `docker-compose.yml`:
   ```yaml
   services:
     signal-cli:
       image: bbernhard/signal-cli-rest-api:latest
       restart: unless-stopped
       environment:
         - MODE=normal
       volumes:
         - signal-data:/home/.local/share/signal-cli
       networks:
         - clean-data-net
       healthcheck:
         test: ["CMD", "curl", "-f", "http://localhost:8080/v1/about"]
         interval: 30s
         timeout: 10s
         retries: 3
         start_period: 15s

     clean-data:
       build: .
       restart: unless-stopped
       depends_on:
         signal-cli:
           condition: service_healthy
       env_file:
         - .env
       environment:
         - SIGNAL_CLI_URL=http://signal-cli:8080
         - TEMP_DIR=/tmp/clean-data
       tmpfs:
         - /tmp/clean-data:size=512M,mode=0700,uid=1000,gid=1000
       networks:
         - clean-data-net
       healthcheck:
         test: ["CMD", "python3", "-c", "import os; os.kill(1, 0)"]
         interval: 30s
         timeout: 5s
         retries: 3

   volumes:
     signal-data:

   networks:
     clean-data-net:
       driver: bridge
   ```

2. Run: `docker compose config` → Expect: valid YAML, no errors

**Verification:** `docker compose config`
**Acceptance criteria:**
- [ ] signal-cli uses official image with named volume
- [ ] clean-data builds from local Dockerfile
- [ ] SIGNAL_CLI_URL points to signal-cli service via Docker DNS
- [ ] TEMP_DIR is a tmpfs mount (512MB, mode 0700)
- [ ] signal-cli port NOT exposed to host
- [ ] Both services have health checks and restart policy
- [ ] clean-data waits for signal-cli to be healthy before starting

---

### Task 4: Update .env.example for Docker

**Independent:** Yes
**Estimated scope:** Small (1 file)

**Files:**
- Modify: `.env.example`

**Steps:**
1. Update `.env.example` to document Docker-specific values:
   ```
   # Signal bot configuration
   SIGNAL_PHONE_NUMBER=+972XXXXXXXXX

   # These are set automatically by docker-compose, but can be overridden:
   # SIGNAL_CLI_URL=http://signal-cli:8080
   # TEMP_DIR=/tmp/clean-data

   MAX_FILE_SIZE_MB=25
   PROCESSING_TIMEOUT=300

   # Optional: comma-separated list of allowed phone numbers
   # ALLOWED_SENDERS=+972501234567,+972509876543
   ```

**Verification:** `cat .env.example`
**Acceptance criteria:**
- [ ] Documents that SIGNAL_CLI_URL and TEMP_DIR are set by docker-compose
- [ ] ALLOWED_SENDERS documented with example

---

### Task 5: Update README with Docker instructions

**Independent:** No — depends on Tasks 2, 3
**Estimated scope:** Small (1 file)

**Files:**
- Modify: `README.md`

**Steps:**
1. Add a "Docker Deployment" section after the existing "Setup" section:

   **Content to add:**
   - Prerequisites: Docker and Docker Compose
   - Register Signal number: `docker compose run signal-cli signal-cli -u +YOURPHONE register`
   - Verify: `docker compose run signal-cli signal-cli -u +YOURPHONE verify CODE`
   - Configure `.env`
   - Start: `docker compose up -d`
   - View logs: `docker compose logs -f clean-data`
   - Stop: `docker compose down`
   - Note that signal-cli data persists in a Docker volume
   - Note that temp files are on tmpfs and lost on restart (by design)

2. Verify the README renders correctly

**Verification:** `grep -c "Docker" README.md` → at least 5 matches
**Acceptance criteria:**
- [ ] Docker deployment instructions are clear and complete
- [ ] Signal registration steps documented
- [ ] tmpfs behavior documented

---

## Dependency Graph

```
Task 1 (.dockerignore, independent) ──┐
Task 2 (Dockerfile, independent)  ────┼──► Task 5 (README update)
Task 3 (docker-compose, after 2)  ────┘
Task 4 (.env.example, independent) ───┘
```

**Parallelizable:** Tasks 1, 2, 4
**Sequential:** Task 3 (after 2), Task 5 (after 2, 3)

---

## Verification Summary

| Task | Verification Command | Expected Output |
|------|---------------------|-----------------|
| 1 | `cat .dockerignore` | File with expected exclusions |
| 2 | `docker build -t clean-data .` | Build succeeds |
| 3 | `docker compose config` | Valid YAML, no errors |
| 4 | `cat .env.example` | Updated with Docker notes |
| 5 | `grep -c "Docker" README.md` | At least 5 matches |
| All | `docker compose up -d && docker compose ps` | Both services running/healthy |
