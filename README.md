# Assure

**What it does:** You give it a source and a question. It writes a document, then verifies every claim against the source. Each claim carries a verification state (supported, partial, contradicted, not confirmed, no source). A separate adversarial review (Red-Hat) checks if claims are defensible.

**Three checks:**
- Anchoring: is there a source for this claim?
- Entailment: does the source support this claim?
- Numeral audit: do the numbers reconcile? (Z3; a solver timeout is reported as `TIMEOUT`, never as a pass)

**Parsure** is the intake side of the same product: every upload (PDF, scan, phone photo, screenshot, image) gets a per-page quality score, a document type, extracted fields with a stated confidence basis, and a review/dispute trail. Parsure computes and flags; Assure displays, decides and delivers. Contract: [docs/parsure.md](docs/parsure.md). Specs: [assure_parsure_v1_icp_spec.md](assure_parsure_v1_icp_spec.md), [assure_ui_revisions.md](assure_ui_revisions.md).

**Staging:** `https://staging.getassureai.com` — the shell (`prototype/`) behind the Cloudflare tunnel; Flask serves `/api/*`.

## Architecture

```
Shell gate (prototype/dev-server.py, host port 80)         the product UI; proxies /api/* and /signin,/signup,/signout,/parsing,/connect
  → Gunicorn (prompt_matrix.web:app :8765)               Flask API
    → PostgreSQL (DATABASE_URL)                          the only database (SQLite is gone; db/pg_compat.py runs the SQL)
    → Redis (REDIS_URL)                                  Celery broker/results, rate limits, debounce locks
    → Celery worker (queue `parse`)                      document intake, OCR, quality probe, Z3, Red-Hat — never on the web tier
    → Object store: S3 (ASSURE_S3_BUCKET) or <ASSURE_DATA_DIR>/objects   uploads, JDF mirror, OMP artifacts
    → Ollama (:11434, `ollama` volume)                   every model call, on the same host; no provider key
```

One image runs web and worker; `docker compose up` gives the whole topology on a laptop or on one EC2 instance. `infra/terraform` runs the same image on ECS Fargate (ARM64, On-Demand only) for the managed-services variant. Details: [docs/scale_architecture.md](docs/scale_architecture.md).

**Ports:** `80` shell gate — the product UI (`SHELL_BIND`/`SHELL_PORT` in `.env` move it) · `8765` Flask API · `11434` Ollama · `5432`/`6379` PostgreSQL/Redis (dev compose only) · `8890` venv Flask started by hand.

## Quick start

```bash
# laptop — everything, including the models
./scripts/gen-env.sh local && docker compose up -d --build
open http://localhost/                # shell on port 80; enter SHELL_ACCESS_KEY (.env)

# server (EC2 Graviton, Ubuntu 24.04 arm64) — same stack, shell on 0.0.0.0:80
./scripts/gen-env.sh ec2              # asks: AWS key id + secret (hidden, empty = none), region, S3 bucket, port, models; writes .env, runs nothing
docker compose up -d --build          # runbook: docs/deploy-single-ec2.md; one command, builds the image once
docker compose ps                     # ollama-pull "Exited (0)" = models present; app/worker start after it
./scripts/errors.sh                   # services + health + every error line, one command
# open http://<host>/ and enter SHELL_ACCESS_KEY from .env

# venv (what CI does)
./scripts/install.sh                  # or: uv sync --extra dev
docker compose -f docker-compose.dev.yml up -d
export DATABASE_URL=postgresql://assure:assure@localhost:5432/assure REDIS_URL=redis://localhost:6379/0
.venv/bin/python -m prompt_matrix.web --web --no-browser --port 8890
```

**Models are local on every compose target.** `docker compose up` starts the `ollama` service; the one-shot `ollama-pull` downloads `qwen2.5:1.5b` and `llama3.2:1b` (~2.3 GB) into the `ollama` volume the first time and only checks them on later starts (no network needed). `assure-app` and `assure-worker` wait for that check, so the first compile never meets a missing model. With `ASSURE_LLM_BACKEND=ollama` (the compose default) compile, lock inference, entailment, Red-Hat, surgical edit and Compare all go to that container; no OpenRouter/DeepSeek/Anthropic key is read. CPU inference: a compile takes minutes. Bigger models (`qwen2.5:7b`, ~4.7 GB RAM) go in `.env` as `ASSURE_OLLAMA_MODEL(_B)`; `OLLAMA_CONTEXT_LENGTH` (default 8192) sizes the prompt window.

Staging/production overlays (`docker-compose.staging.yml`, `docker-compose.prod.yml`) leave `ASSURE_LLM_BACKEND` empty, use the cloud policies in `prompt_matrix/cost_governance.py`, and put the Ollama pair behind the `local-llm` profile (`COMPOSE_PROFILES=local-llm` brings it back):

```bash
docker compose -f docker-compose.yml -f docker-compose.staging.yml \
  --env-file .env.staging --env-file .env.staging.local up -d
```

> **Docker Desktop (Mac/Windows):** the stack needs ~6 GB inside the Docker VM (Ollama ~1.5 GB per loaded model, PostgreSQL, Redis, web, worker). Docker Desktop → Settings → Resources → Memory **≥ 10 GB**, or the VM runs out of memory and the daemon restarts every container. On a Linux host (EC2) there is no VM; size the instance instead (`m7g.large` minimum, `m7g.xlarge` for a 7B model).

**Documents:** uploads answer **202** with a `task_id`; `GET /api/tasks/<task_id>` and `/api/projects/<id>/ingest-jobs` report parser, OCR confidence, Z3 verdict and Red-Hat status. Accepted inputs: PDF, PNG/JPG/TIFF/BMP images and text files. Scans and images are OCR'd by jdf-cli's bundled tesseract; Textract only when it is configured or OCR reads nothing (spend is capped per month). After verification the intake report (`GET /api/projects/<id>/parsure`) carries page quality, document type, fields with `confidence_basis`, `field_state` / `routing_action`, disputes and the audit log.

**Key variables:** `DATABASE_URL`, `REDIS_URL`/`CELERY_BROKER_URL`, `ASSURE_DATA_DIR`, `ASSURE_S3_BUCKET` + `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` (or the IAM role / Sources panel), `PARSE_ASYNC`, `SUBSTRATE_ASYNC_UPLOAD`, `PARSER_SCAN_BACKEND`, `JDF_OCR`, `ASSURE_LLM_BACKEND`, `ASSURE_OLLAMA_MODEL(_B)`, `OLLAMA_CONTEXT_LENGTH`, `ASSURE_EDITION`, `CLERK_*`, `ASSURE_CLERK_ONLY`, `SHELL_ACCESS_KEY`, `PEM_SECRET_KEY`, `ENVIRONMENT`. Full list: `.env.example`.

---

## Routes (public surface through the shell gate)

- `/` - Shell (prototype/index.html) — the Assure verification workspace
- `/parsing` - Parsure intake page (what came in, its quality, what needs attention)
- `/signin`, `/signup`, `/signout` - Auth (Clerk, or the self-hosted message)
- `/connect` - Connect a model provider (unused with local models)
- `/api/*` - API (projects, substrate, jdf ingest/search, draft/stream, redhat, export, tasks, ingest-jobs, parsure, integrations/aws, health)
- `/static/*`, `/favicon.svg`

Removed: `/workbench`, `/app`, `/compose`, `/history`, `/learn`, `/library`, `/architecture` — the legacy Flask workbench; the shell is the only UI.

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
ASSURE_LLM_BACKEND               ollama (compose default) | bedrock | empty (cloud policies)
ASSURE_OLLAMA_MODEL(_B), OLLAMA_API_BASE, OLLAMA_CONTEXT_LENGTH
OPENROUTER_API_KEY, DEEPSEEK_API_KEY, ANTHROPIC_API_KEY   cloud models (overlays only)
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
- `prompt_matrix/routers/` - `jdf_routes.py` (import-pdf, presign), `substrate.py` (Sources vault), `jdf_memory_routes.py` (`/jdf/ingest`, search), `draft.py` (compile stream), `inquire_stream.py` (surgical edit), `ingest_jobs_routes.py`, `parsure_routes.py` (intake reports, corrections, disputes, exports, audit log), `async_tasks_routes.py`, `integrations_routes.py`, `health.py`
- `prompt_matrix/services/` - `pdf_ingest.py` (intake pipeline), `parser_router.py` (the one routing decision, incl. material/modality detection and Laya triage), `quality_probe.py` (visual quality, page quality score, quality-weighted confidence, signature/number quality), `laya.py` (rule-based triage), `field_extractor.py` (classification, ICP fields, VIN, plausibility rules, decision policy), `v1_orchestrator.py` (the intake report), `jdf_converter.py` (jdf-cli + OCR), `verification.py` (Z3 + Red-Hat), `jdf_memory.py` (search index), `object_store.py`, `aws_integration.py`, `redis_client.py`
- `prompt_matrix/db/` - `pg_compat.py` (PostgreSQL backend for the SQLite-dialect SQL), `connection.py` (migrations), repositories (`parsure_repository.py` holds reports, corrections, disputes, audit events)
- `prompt_matrix/tasks/` - Celery tasks (`parse_tasks.py`, `substrate_tasks.py`, `redhat.py`)
- `prompt_matrix/cloud_auth.py` - Clerk, `is_self_hosted()`, `clerk_configured()`
- `prompt_matrix/cost_governance.py` - model policies per task, `ASSURE_LLM_BACKEND`

**Templates (keep):** `auth.html`, `parsing.html` (Parsure), `connect.html`.

**Prototype (the UI):** `prototype/dev-server.py` (gate + proxy), `index.html`, `shell.js`, `shell.css`, `about.html`, `favicon.svg`.

**Infra:** `Dockerfile`, `docker-compose*.yml`, `scripts/gen-env.sh`, `infra/terraform/`, `.github/workflows/`. Desktop build: `./scripts/build-desktop.sh` (needs a reachable PostgreSQL). Marketing site sync: `./scripts/sync-webpage.sh`.

**Legacy, don't extend:** `landing/`, `prompt_matrix/templates/base.html` + `static/founder_workbench.css` (the old design system; the workbench template that used it was deleted 2026-09-24).

---

## UI systems

**Prototype UI (`prototype/`) — the real Assure UI.** One accent colour (ink `#0A0A0A`), four semantic colours, everything else monochrome; serif for document body, sans for chrome, mono for data; 8px grid. Header: brand and document name | one status chip | one primary action + one overflow menu. Left rail: Workspaces, Sources, Analytics (→ Parsure), Settings. Layout: 48px rail | 280px left pane | 1fr document | 320px inspector | 48px rail.

```css
:root {
  --ink: #0A0A0A; --paper: #FAFAF7; --surface: #FFFFFF; --rule: #E8E8E4; --muted: #6B6B66;
  --verified: #0F6E3F; --partial: #B8730E; --unverified: #9A9A94; --contradicted: #A32D2D;
}
```

**Parsure page (`/parsing`, `prompt_matrix/templates/parsing.html`).** Self-contained CSS on the same tokens; it does not extend `base.html`. One summary line (documents, pages, average quality, needs attention, ready), one Upload action, evidence cards (source type · modality, document type, quality, review count, calm amber warnings; parser badge secondary), details on expand. Backend: `web.py::parsing_page()` joins `list_substrate_for_project` with the intake reports.

Do NOT use `founder_workbench.css` for new work.

---

## Access key & auth

- Shell gate: with `SHELL_ACCESS_KEY` set, `/auth` asks for it (cookie `assure_shell_key`, `X-Shell-Key`, Bearer). Unset → the gate serves without a key (staging).
- Clerk: `CLERK_*` keys plus `ASSURE_CLERK_ONLY=1` for per-user identity; `ASSURE_EDITION=self-hosted` skips Clerk.
- `PUT/DELETE /api/integrations/aws` is admin-only.

## Branches

| Branch | Purpose |
|--------|---------|
| `staging` | GitHub default. Staging EC2 deploy. |
| `staging-v1` | V1 (Parsure + UI revision) integration branch. |
| `main` | Production lineage. |
| `webpage` | Cloudflare Worker marketing site. |

## Tests

```bash
docker compose -f docker-compose.dev.yml up -d
DATABASE_URL=postgresql://assure:assure@localhost:5432/assure .venv/bin/pytest tests/ -q \
  --ignore=tests/e2e --ignore=tests/playwright --ignore=tests/quality_check
python scripts/validate_golden_set.py      # Parsure golden set: per-field / per-type accuracy
```
Every test gets its own PostgreSQL schema; CI runs the same against a PostgreSQL service container plus the Playwright suite.
