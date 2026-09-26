# syntax=docker/dockerfile:1
# Multi-stage: sentry bundle → Python deps → node/jdf CLI → slim runtime.
# One image serves both roles: the web tier (gunicorn, default CMD) and the
# parse/verification worker (celery, see docker-compose.yml (assure-worker service) / the ECS
# worker service). The runtime carries every parse path: PyMuPDF (probe and
# fallback), node + @uurtech/jdf-cli with its bundled tesseract.js OCR (the
# English language data is baked in so a scan never waits on a CDN), Z3, and
# boto3 for Textract/S3. Database is PostgreSQL (psycopg), broker is Redis or
# SQS. Built for linux/arm64 (Graviton) and linux/amd64.

FROM node:22-slim AS sentry
WORKDIR /build
COPY package.json package-lock.json ./
COPY scripts/bundle-sentry.mjs scripts/bundle-sentry.mjs
COPY prompt_matrix/static/src/ prompt_matrix/static/src/
RUN npm ci && npm run bundle:sentry

FROM python:3.11-slim AS builder
WORKDIR /app
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && pip install --upgrade pip \
    && pip install --prefer-binary -r requirements.txt gunicorn gevent flask-cors httpx asgiref \
    && apt-get purge -y gcc \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

FROM python:3.11-slim AS jdfcli
# The JDF CLI is TypeScript; its bin shim is `#!/usr/bin/env node`. The app
# shells out to it from services/jdf_converter.py, so the runtime image needs
# both the interpreter and the package.
WORKDIR /jdfcli
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*
ARG JDF_CLI_VERSION=0.2.3
RUN npm install -g @uurtech/jdf-cli@${JDF_CLI_VERSION}

FROM python:3.11-slim AS runtime
WORKDIR /app
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PORT=8765 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    USE_DOCLING=0 \
    HOME=/root \
    ASSURE_LOG_STDOUT=1 \
    JDF_OCR=tesseract \
    PARSE_ASYNC=1

# poppler/curl as before, plus WeasyPrint's native stack (Pango, Cairo,
# GDK-PixBuf, libffi, the MIME database) and DejaVu so the dossier has a real
# font to embed. WeasyPrint is the PDF renderer the app requires: until
# 2026-09-26 the image had neither it nor a Playwright browser, and every PDF
# export fell through to a Helvetica text writer (customer QA: a "dossier" that
# named the missing engines on page 1). services/verification_dossier.render_pdf
# now refuses to render without an engine, so the runtime must carry one.
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils curl ca-certificates \
        libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 libffi8 \
        shared-mime-info fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# tesseract.js language data for jdf-cli's OCR (`jdf convert --ocr tesseract`).
# tesseract.js caches it under $HOME/.cache/jdf/tesseract and would otherwise
# download it from jsdelivr on the first scanned page of every fresh container.
ARG TESSERACT_LANGS="eng"
RUN mkdir -p /root/.cache/jdf/tesseract \
    && for lang in ${TESSERACT_LANGS}; do \
         curl -fsSL "https://cdn.jsdelivr.net/npm/@tesseract.js-data/${lang}/4.0.0/${lang}.traineddata.gz" \
           | gunzip > "/root/.cache/jdf/tesseract/${lang}.traineddata"; \
       done \
    && ls -la /root/.cache/jdf/tesseract

# node + the jdf CLI. jdf_converter.py looks for `jdf` on PATH and falls back to
# /opt/node-*/bin/jdf; both now resolve.
COPY --from=jdfcli /usr/bin/node /usr/bin/node
COPY --from=jdfcli /usr/lib/node_modules /usr/lib/node_modules
RUN ln -sf /usr/lib/node_modules/@uurtech/jdf-cli/dist/index.js /usr/local/bin/jdf \
    && chmod +x /usr/lib/node_modules/@uurtech/jdf-cli/dist/index.js

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .
COPY --from=sentry /build/prompt_matrix/static/sentry.bundle.js prompt_matrix/static/sentry.bundle.js

# Build metadata args - placed AFTER dependency installation to preserve their cache
ARG BUILD_SHA=unknown
ARG BUILD_BRANCH=unknown
ARG BUILD_TIME=unknown

ENV ASSURE_BUILD_SHA=${BUILD_SHA} \
    ASSURE_BUILD_BRANCH=${BUILD_BRANCH} \
    ASSURE_BUILD_TIME=${BUILD_TIME}

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8765') + '/api/health', timeout=3)"

# Web tier. gthread rather than gevent: the request path still calls native
# code (psycopg, PyMuPDF probe, occasional Z3) that gevent cannot yield around,
# and parsing itself is off this tier (PARSE_ASYNC). Timeouts are explicit —
# gunicorn's 30 s default would kill a worker mid-upload behind a slow client.
# The worker role overrides CMD: celery -A prompt_matrix.celery_app:celery_app worker -Q parse,default
CMD ["sh", "-c", "exec gunicorn --worker-class gthread --workers ${GUNICORN_WORKERS:-2} --threads ${GUNICORN_THREADS:-8} --timeout ${GUNICORN_TIMEOUT:-120} --graceful-timeout 30 --keep-alive 5 --access-logfile - --bind 0.0.0.0:${PORT:-8765} prompt_matrix.web:app"]
