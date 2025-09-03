# syntax=docker/dockerfile:1.5

# -------- Base layer with deps + code --------
FROM python:3.11-slim AS base
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System deps (minimal)
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl \
 && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt /app/
RUN pip install -U pip && pip install -r requirements.txt

# Copy code (matches your repo layout)
COPY lore_ingest/ /app/lore_ingest/
COPY service/     /app/service/
COPY cli/         /app/cli/
COPY pyproject.toml /app/
COPY thoughts.md      /app/
COPY docs/          /app/docs/

# Runtime env + dirs
ENV PYTHONPATH=/app \
    DB_PATH=/app/data/tropes.db \
    INBOX=/app/inbox \
    SUCCESS_DIR=/app/success \
    FAIL_DIR=/app/fail

RUN mkdir -p /app/data /app/inbox /app/success /app/fail

# -------- HTTP API --------
FROM base AS http
EXPOSE 8099
CMD ["uvicorn", "service.http_app:app", "--host", "0.0.0.0", "--port", "8099"]

# -------- Folder watcher --------
FROM base AS watcher
CMD ["python", "-m", "service.watcher"]

# -------- Temporal worker (optional) --------
FROM base AS worker
CMD ["python", "-m", "service.temporal_worker"]

