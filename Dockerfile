# syntax=docker/dockerfile:1
# Multi-stage: sentry bundle → Python deps → slim runtime.
# Keep CPU ML wheels out of this image; runtime deps are requirements.txt plus gunicorn.

FROM node:22-slim AS sentry
WORKDIR /build
COPY package.json package-lock.json ./
COPY scripts/bundle-sentry.mjs scripts/bundle-sentry.mjs
COPY scripts/bundle-tiptap.mjs scripts/bundle-tiptap.mjs
COPY prompt_matrix/static/src/ prompt_matrix/static/src/
RUN npm ci && npm run bundle:sentry && npm run bundle:tiptap

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

FROM python:3.11-slim AS runtime
WORKDIR /app
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PORT=8765 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

ARG ASSURE_BUILD_SHA=unknown
ENV ASSURE_BUILD_SHA=${ASSURE_BUILD_SHA}

RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .
COPY --from=sentry /build/prompt_matrix/static/sentry.bundle.js prompt_matrix/static/sentry.bundle.js
COPY --from=sentry /build/prompt_matrix/static/tiptap.bundle.js prompt_matrix/static/tiptap.bundle.js

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8765') + '/api/health', timeout=3)"

CMD ["sh", "-c", "exec gunicorn --worker-class gevent --workers ${GUNICORN_WORKERS:-4} --worker-connections ${GUNICORN_WORKER_CONNECTIONS:-100} --bind 0.0.0.0:${PORT:-8765} prompt_matrix.web:app"]
