# syntax=docker/dockerfile:1
# Multi-stage: sentry bundle → Python deps → node/jdf CLI → slim runtime.
# Runtime carries every parse path the app can take: poppler, PyMuPDF and
# Docling from requirements.txt, plus node and the jdf CLI for
# services/jdf_converter.py.

FROM node:22-slim AS sentry
WORKDIR /build
COPY package.json package-lock.json ./
COPY scripts/bundle-sentry.mjs scripts/bundle-sentry.mjs
COPY scripts/bundle-tiptap.mjs scripts/bundle-tiptap.mjs
COPY scripts/bundle-jdf.mjs scripts/bundle-jdf.mjs
COPY prompt_matrix/static/src/ prompt_matrix/static/src/
RUN npm ci && npm run bundle:sentry && npm run bundle:tiptap && npm run bundle:jdf

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
RUN npm install -g @uurtech/jdf-cli

FROM python:3.11-slim AS runtime
WORKDIR /app
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PORT=8765 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    USE_DOCLING=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

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
COPY --from=sentry /build/prompt_matrix/static/tiptap.bundle.js prompt_matrix/static/tiptap.bundle.js

# Build metadata args - placed AFTER dependency installation to preserve their cache
ARG BUILD_SHA=unknown
ARG BUILD_BRANCH=unknown
ARG BUILD_TIME=unknown

ENV ASSURE_BUILD_SHA=${BUILD_SHA} \
    ASSURE_BUILD_BRANCH=${BUILD_BRANCH} \
    ASSURE_BUILD_TIME=${BUILD_TIME}

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8765') + '/api/health', timeout=3)"

CMD ["sh", "-c", "exec gunicorn --worker-class gevent --workers ${GUNICORN_WORKERS:-4} --worker-connections ${GUNICORN_WORKER_CONNECTIONS:-100} --bind 0.0.0.0:${PORT:-8765} prompt_matrix.web:app"]
