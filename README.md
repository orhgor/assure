# Assure - Document Verification Platform

**What it does:** You give it a source and a question. It writes a document, then verifies every claim against the source. Each claim carries a verification state (supported, partial, contradicted, not confirmed, no source). A separate adversarial review (Red-Hat) checks if claims are defensible.

**Three checks:**
- Anchoring: is there a source for this claim?
- Entailment: does the source support this claim?
- Numeral audit: do the numbers reconcile? (Z3; a solver timeout is reported as `TIMEOUT`, never as a pass)

**Staging:** `https://staging.getassureai.com`

---

## Architecture

```
Cloudflare Tunnel (staging.getassureai.com)
  → Shell gate (prototype/dev-server.py :8891)        the product UI; proxies /api/* and a few pages
    → Gunicorn (prompt_matrix.web:app :8765)          Flask API
      → PostgreSQL (DATABASE_URL)                     the only database; SQLite is gone
      → Redis (REDIS_URL)                             Celery broker/results, rate limits, debounce locks
      → Celery worker (queue `parse`)                 PDF parse, OCR, Z3, Red-Hat — never on the web tier
      → Object store: S3 (ASSURE_S3_BUCKET) or <ASSURE_DATA_DIR>/objects
      → Models: Ollama container locally (ASSURE_LLM_BACKEND=ollama); OpenRouter/DeepSeek/Bedrock in production
```

One image runs web and worker; `docker compose up` gives the whole topology on a laptop, `infra/terraform` runs it on ECS Fargate (ARM64, On-Demand). Details: [docs/scale_architecture.md](docs/scale_architecture.md).

**Ports:**
- `8891` - Shell gate (entry point) - **keep**
- `8765` - Gunicorn / Flask API - **keep**
- `5432`, `6379` - PostgreSQL, Redis (docker-compose.dev.yml exposes them for the venv and pytest)
- `11434` - Ollama (local models)
- `8890` - venv Flask started by hand (`python -m prompt_matrix.web --web --port 8890`) - dev only

---

## Quick start

```bash
# full stack, zero config (PostgreSQL, Redis, web, worker, shell, ollama + model pull)
docker compose up -d --build
open http://127.0.0.1:8891            # shell; enter SHELL_ACCESS_KEY (.env) when one is set

# staging environment on a laptop
docker compose -f docker-compose.yml -f docker-compose.staging.yml \
  --env-file .env.staging --env-file .env.staging.local up -d
# .env.staging is the tracked staging file; .env.staging.local (ignored) holds
# POSTGRES_PASSWORD, PEM_SECRET_KEY, APP_IMAGE=assure-app:local and the local model switch.

# venv (what CI does)
./scripts/install.sh                      # or: uv sync --extra dev
docker compose -f docker-compose.dev.yml up -d
export DATABASE_URL=postgresql://assure:assure@localhost:5432/assure REDIS_URL=redis://localhost:6379/0
.venv/bin/python -m prompt_matrix.web --web --no-browser --port 8890
```

Models locally: `docker compose up` starts an `ollama` service and `ollama-pull` fetches `qwen2.5:3b` and `llama3.2:3b` once (~4 GB). With `ASSURE_LLM_BACKEND=ollama` (compose default) every model call — compile, lock inference, entailment, Red-Hat, surgical edit, Compare — goes to that container; no provider key. CPU inference: a compile takes minutes. Production/staging leave `ASSURE_LLM_BACKEND` empty and use the cloud policies in `prompt_matrix/cost_governance.py`.

Documents: uploads answer **202** with a `task_id`; `GET /api/tasks/<task_id>` and `/api/projects/<id>/ingest-jobs` report parser, OCR confidence, Z3 verdict and Red-Hat status. Scans are OCR'd by jdf-cli's bundled tesseract; Textract only when OCR fails or reads nothing.

---

## Routes (public surface through the shell gate)

### Keep (proxied, working)
- `/` - Shell (prototype/index.html)
- `/signin`, `/signup`, `/signout` - Auth (Clerk, or the self-hosted message)
- `/parsing` - Parsing results dashboard
- `/connect` - Connect a model provider
- `/api/*` - API (projects, substrate, jdf ingest/search, draft/stream, redhat, export, tasks, ingest-jobs, integrations/aws, health)
- `/static/*`, `/favicon.svg`

### Removed (legacy Flask workbench)
- `/workbench`, `/app`, `/compose`, `/history`, `/learn`, `/library`, `/architecture` — routes deleted on staging; the shell is the only UI.

---

## Environment

**Staging (`.env.staging`, tracked):** `ASSURE_EDITION=self-hosted`, `ENVIRONMENT=staging`, `APP_HOST`, `SHELL_ACCESS_KEY`, `ASSURE_REQUIRE_LOGIN=false`, `ASSURE_MAX_PAGES=200`, `SUBSTRATE_INGEST_SECRET`, `CLOUDFLARE_TUNNEL_TOKEN`, `CLOUDFLARE_TUNNEL_ID`, `ASSURE_EDGE_WORKER_URL`.

**Key variables (code):**
```
DATABASE_URL                     PostgreSQL DSN (required)
REDIS_URL, CELERY_BROKER_URL     Redis locally; sqs:// on AWS
ASSURE_DATA_DIR, ASSURE_S3_BUCKET, AWS_ACCESS_KEY_ID/SECRET  object store (or the IAM role / Sources panel)
PARSE_ASYNC, SUBSTRATE_ASYNC_UPLOAD  1 = queued to the worker (default with a broker)
PARSER_SCAN_BACKEND, JDF_OCR     jdf-ocr (tesseract, default) | textract
ASSURE_LLM_BACKEND               ollama (local) | empty (cloud policies)
ASSURE_OLLAMA_MODEL(_B), OLLAMA_API_BASE
OPENROUTER_API_KEY, DEEPSEEK_API_KEY, ANTHROPIC_API_KEY   cloud models
ASSURE_EDITION / PEM_EDITION     "self-hosted" = no Clerk
CLERK_PUBLISHABLE_KEY, CLERK_SECRET_KEY, ASSURE_CLERK_ONLY
SHELL_ACCESS_KEY, UPSTREAM_BASE, PORT, HOST   shell gate
PEM_SECRET_KEY, ENVIRONMENT      required on servers
```
Full list with meanings: `.env.example`.

---

## Files

**Core:**
- `prompt_matrix/web.py` - Flask app factory, route registration
- `prompt_matrix/routers/` - `jdf_routes.py` (import-pdf, presign), `substrate.py` (Sources vault), `jdf_memory_routes.py` (`/jdf/ingest`, search), `draft.py` (compile stream), `inquire_stream.py` (surgical edit), `ingest_jobs_routes.py`, `async_tasks_routes.py`, `integrations_routes.py`, `health.py`
- `prompt_matrix/services/` - `pdf_ingest.py` (parse pipeline), `parser_router.py`, `jdf_converter.py` (jdf-cli + OCR), `verification.py` (Z3 + Red-Hat), `jdf_memory.py` (search index), `object_store.py`, `aws_integration.py`, `redis_client.py`
- `prompt_matrix/db/` - `pg_compat.py` (PostgreSQL backend for the SQLite-dialect SQL), `connection.py` (migrations), repositories
- `prompt_matrix/tasks/` - Celery tasks (`parse_tasks.py`, `substrate_tasks.py`, `redhat.py`)
- `prompt_matrix/cloud_auth.py` - Clerk, `is_self_hosted()`, `clerk_configured()`
- `prompt_matrix/cost_governance.py` - model policies per task, `ASSURE_LLM_BACKEND`

**Templates (keep):** `auth.html`, `parsing.html`, `connect.html`.

**Prototype (the UI):** `prototype/dev-server.py` (gate + proxy), `index.html`, `shell.js`, `shell.css`, `about.html`, `favicon.svg`.

**Infra:** `Dockerfile`, `docker-compose*.yml`, `infra/terraform/`, `.github/workflows/`. Desktop build: `./scripts/build-desktop.sh` (needs a reachable PostgreSQL). Marketing site sync: `./scripts/sync-webpage.sh`.

**Legacy, don't extend:** `worker/` (Cloudflare edge worker, being retired), `landing/`, `prompt_matrix/templates/index.html` (workbench template; its routes are gone).

---

## Access Key & Auth

- Shell gate: with `SHELL_ACCESS_KEY` set, `/auth` asks for it (cookie `assure_shell_key`, `X-Shell-Key`, Bearer). Unset → the gate serves without a key (staging).
- Clerk: `CLERK_*` keys plus `ASSURE_CLERK_ONLY=1` for per-user identity; `ASSURE_EDITION=self-hosted` skips Clerk.
- `PUT/DELETE /api/integrations/aws` is admin-only.

## Tests

```bash
docker compose -f docker-compose.dev.yml up -d
DATABASE_URL=postgresql://assure:assure@localhost:5432/assure .venv/bin/pytest tests/ -q \
  --ignore=tests/e2e --ignore=tests/playwright --ignore=tests/quality_check
```
Every test gets its own PostgreSQL schema; CI runs the same against a PostgreSQL service container plus a Playwright suite.
