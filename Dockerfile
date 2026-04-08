# Stage 1 — builder: install dependencies and download NLP models
# Pin to digest for reproducibility. Update periodically after review.
FROM python:3.11-slim@sha256:7ae2d10e4bdc6f69ba2daf031647568fec08f3191621d7a5c8760abb236d16ab AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir -r requirements.txt

# Download and cache NLP models
RUN python -m spacy download en_core_web_sm

# Stage 2 — runtime: slim image with only what's needed
FROM python:3.11-slim@sha256:7ae2d10e4bdc6f69ba2daf031647568fec08f3191621d7a5c8760abb236d16ab

# Create non-root user with explicit UID/GID to match tmpfs mount in docker-compose
RUN groupadd -r -g 1000 cleandata && useradd -r -g cleandata -u 1000 -m cleandata

WORKDIR /app

# Copy venv from builder (spaCy model is installed inside the venv)
COPY --from=builder /opt/venv /opt/venv

# Copy application source
COPY config.py main.py signal_bot.py word_session_store.py ./
COPY processor/ ./processor/
COPY utils/ ./utils/

# Set environment
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Fix ownership
RUN chown -R cleandata:cleandata /app /home/cleandata

USER cleandata

ENTRYPOINT ["python3", "main.py"]
