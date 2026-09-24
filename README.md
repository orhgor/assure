# Assure

**What it does:** You give it a source and a question. It writes a document, then verifies every claim against the source. Each claim carries a verification state (supported, partial, contradicted, not confirmed, no source). A separate adversarial review (Red-Hat) checks if claims are defensible.

**Three checks:**
- Anchoring: is there a source for this claim?
- Entailment: does the source support this claim?
- Numeral audit: do the numbers reconcile? (Z3; a solver timeout is reported as `TIMEOUT`, never as a pass)

**Staging:** `https://staging.getassureai.com` — the shell (`prototype/`) behind the Cloudflare tunnel; Flask serves `/api/*`.

## Architecture

```
Cloudflare Tunnel (staging.getassureai.com)
  → Shell gate (prototype/dev-server.py :8891)        the product UI; proxies /api/* and /signin,/signup,/signout,/parsing,/connect
    → Gunicorn (prompt_matrix.web:app :8765)          Flask API
      → PostgreSQL (DATABASE_URL)                     the only database (SQLite is gone; db/pg_compat.py runs the SQL)
      → Redis (REDIS_URL)                             Celery broker/results, rate limits, debounce locks
      → Celery worker (queue `parse`)                 PDF parse, OCR, Z3, Red-Hat — never on the web tier
      → Object store: S3 (ASSURE_S3_BUCKET) or <ASSURE_DATA_DIR>/objects   uploads, JDF mirror, OMP artifacts
      → Models: Ollama container locally (ASSURE_LLM_BACKEND=ollama); OpenRouter/DeepSeek (Bedrock) in production
```

One image runs web and worker; `docker compose up` gives the whole topology on a laptop, `infra/terraform` runs it on ECS Fargate (ARM64, On-Demand only). Details: [docs/scale_architecture.md](docs/scale_architecture.md). The Cloudflare R2 edge worker was retired on 2026-09-23; uploads go through the object store on every environment.

**Ports:** `8891` shell gate · `8765` Flask API · `5432`/`6379` PostgreSQL/Redis (dev compose) · `11434` Ollama · `8890` venv Flask started by hand.

## Run

```bash
# laptop: local Ollama models (profile local-llm), shell on 127.0.0.1
./scripts/gen-env.sh local && docker compose up -d --build
# server (EC2 Graviton): Bedrock through the IAM role, shell on 0.0.0.0 — see docs/deploy-single-ec2.md
./scripts/gen-env.sh ec2  && docker compose up -d --build
open http://127.0.0.1:8891            # shell; enter SHELL_ACCESS_KEY (.env) when one is set

# staging environment on a laptop (.env.staging = the box's values, untracked;
# .env.staging.local = POSTGRES_PASSWORD, PEM_SECRET_KEY, APP_IMAGE, ASSURE_LLM_BACKEND=ollama)
docker compose -f docker-compose.yml -f docker-compose.staging.yml \
  --env-file .env.staging --env-file .env.staging.local up -d

# venv (what CI does)
./scripts/install.sh                      # or: uv sync --extra dev
docker compose -f docker-compose.dev.yml up -d
export DATABASE_URL=postgresql://assure:assure@localhost:5432/assure REDIS_URL=redis://localhost:6379/0
.venv/bin/python -m prompt_matrix.web --web --no-browser --port 8890
```

> **Docker Desktop (Mac/Windows):** with the local models profile the stack needs ~6 GB inside the Docker VM
> (Ollama ~1.5 GB per loaded model, PostgreSQL, Redis, web, worker). Docker Desktop → Settings → Resources →
> Memory **≥ 10 GB**, or the VM runs out of memory and the daemon restarts, killing every container.
> On a Linux host (EC2) there is no VM and no such limit; container limits are set in `docker-compose.yml`.

Models locally: the `ollama` service plus a one-shot `ollama-pull` (`qwen2.5:1.5b`, `llama3.2:1b`, ~2.3 GB). With `ASSURE_LLM_BACKEND=ollama` (compose default) every model call — compile, lock inference, entailment, Red-Hat, surgical edit, Compare — goes to that container; no provider key. CPU inference: a compile takes minutes. Production/staging leave `ASSURE_LLM_BACKEND` empty and use the cloud policies in `prompt_matrix/cost_governance.py`.

Documents: uploads answer **202** with a `task_id`; `GET /api/tasks/<task_id>` and `/api/projects/<id>/ingest-jobs` report parser, OCR confidence, Z3 verdict and Red-Hat status. Scans are OCR'd by jdf-cli's bundled tesseract; Textract only when OCR fails or reads nothing.

**Key variables:** `DATABASE_URL`, `REDIS_URL`/`CELERY_BROKER_URL`, `ASSURE_DATA_DIR`, `ASSURE_S3_BUCKET` + `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` (or the IAM role / Sources panel), `PARSE_ASYNC`, `SUBSTRATE_ASYNC_UPLOAD`, `PARSER_SCAN_BACKEND`, `JDF_OCR`, `ASSURE_LLM_BACKEND`, `ASSURE_OLLAMA_MODEL(_B)`, `OPENROUTER_API_KEY`/`DEEPSEEK_API_KEY`/`ANTHROPIC_API_KEY`, `ASSURE_EDITION`, `CLERK_*`, `ASSURE_CLERK_ONLY`, `SHELL_ACCESS_KEY`, `PEM_SECRET_KEY`, `ENVIRONMENT`. Full list: `.env.example`.

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

Models locally: `docker compose up` starts an `ollama` service and `ollama-pull` fetches `qwen2.5:1.5b` and `llama3.2:1b` once (~2.3 GB). With `ASSURE_LLM_BACKEND=ollama` (compose default) every model call — compile, lock inference, entailment, Red-Hat, surgical edit, Compare — goes to that container; no provider key. CPU inference: a compile takes minutes. Production/staging leave `ASSURE_LLM_BACKEND` empty and use the cloud policies in `prompt_matrix/cost_governance.py`.

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

**Legacy, don't extend:** `landing/`, `prompt_matrix/templates/index.html` (workbench template; its routes are gone).

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
=======
## Quick start

The workbench stays on this computer. GitHub `orhgor/assure` is the public site only.

### Desktop (no terminal)

```bash
./scripts/install.sh
./scripts/build-desktop.sh
```

Then double-click `dist/Assure.app` (macOS), `dist\Assure\Assure.exe` (Windows), or `dist/Assure/Assure` (Linux). The browser should open. If it does not, go to [http://127.0.0.1:8765](http://127.0.0.1:8765). There is no public download URL yet.

### From source

```bash
./scripts/install.sh
source prompt_matrix/.venv/bin/activate
assure --web
```

Windows: `scripts\install.ps1`. `pip install prompt-matrix` is not on PyPI yet. Do not clone `orhgor/assure` for the app.

Sign-in is off on this machine by default. Sharing on the LAN (`--host 0.0.0.0`) requires `--http-pass` (or `PEM_HTTP_PASS`). Do not commit the password.

Team edition (unlimited Sends on this machine):

```bash
assure --web --edition team
```

### Ask

Write a question in Compose, or click an example (Compare AWS vs GCP, Summarize a paper, Write a marketing email). Send and get my answer is the default. Copy the prompt keeps the compiled text on this computer.

First run opens Connect so you can paste a key. Copy stays on this computer. A Send goes only to the provider you chose.

Pro is $5 per month on the Pricing page in this tree.

Install, CLI, and MCP details: [prompt_matrix/README.md](prompt_matrix/README.md). Product overview: [prompt_matrix/PEM.md](prompt_matrix/PEM.md). Public landing: [landing/index.html](landing/index.html).

## Testing

**CI** runs `pytest` unit tests plus a **Playwright** suite under `tests/playwright/` (headless Chromium, local embedded Flask — no live model keys).

```bash
uv sync --extra dev
playwright install chromium
pytest tests/playwright/ -v
```

Optional against staging: `ASSURE_BASE_URL=https://staging.getassureai.com pytest tests/playwright/ -v` (requires auth and live compile quota).

## Cloudflare

Two branches, two surfaces:

| Branch | Deploy target | URL |
|--------|---------------|-----|
| `p4-account-wallet` | EC2 Docker — image built in **GitHub Actions**, pulled on EC2 ([deploy flow](docs/deploy-flow.md)) | [getassureai.com](https://getassureai.com) |
| `webpage` | Cloudflare Worker `assure` — 301 → app host | [getassureai.com](https://getassureai.com) → app |

GitHub [`orhgor/assure`](https://github.com/orhgor/assure) default branch is **`staging`**. The JDF Workstation and PEM engine live on **`staging`** and deploy to EC2.

Cloudflare Workers Builds for Worker **`assure`** must connect to **`webpage` only**. A red **Workers Builds: assure** check on an app PR is irrelevant (wrong branch / missing root `wrangler.jsonc`). Fix: [docs/cloudflare-fix.md](docs/cloudflare-fix.md) or `bash scripts/cloudflare/set_workers_branch.sh`.

Marketing deploy: copy `landing/` to a webpage checkout, then push `webpage`:

```bash
./scripts/sync-webpage.sh /path/to/webpage-checkout
```

Worker deploy command: `npx wrangler deploy`. `wrangler.jsonc` must list `assets.directory` (not a Pages `pages_build_output_dir`).

## UI Systems

Assure has two UI systems in this repository. Understanding which is which prevents confusion during development and debugging.

### Prototype UI (`prototype/`)

**The "real" Assure UI.** This is the current design system used by the live application.

```
prototype/
  index.html    # Main workbench shell — the actual app UI
  shell.css     # Design system: tokens, layout grid, typography
  shell.js      # Client-side behavior
  wow_effects.js # Visual effects (laser, stamps, etc.)
  about.html    # About page
  favicon.svg   # App icon
```

**Design principles:**
- One accent colour (ink `#0A0A0A`), four semantic colours, everything else monochrome
- Serif for document body, sans for chrome, mono for data
- 8px grid, no exceptions
- Layout: 48px rail | 280px left pane | 1fr center | 320px right pane | 48px rail
- Header is 56px with version control centered over document column

**CSS variables:**
```css
:root {
  --ink: #0A0A0A;           /* primary accent */
  --paper: #FAFAF7;         /* background */
  --surface: #FFFFFF;       /* cards, panels */
  --rule: #E8E8E4;          /* borders */
  --muted: #6B6B66;         /* secondary text */
  --verified: #0F6E3F;      /* supported */
  --partial: #B8730E;       /* partial */
  --unverified: #9A9A94;    /* unanchored */
  --contradicted: #A32D2D;  /* unsupported */
}
```

### Prompt Matrix UI (`prompt_matrix/templates/`)

**Legacy/older UI system.** Some pages still use this. It uses `founder_workbench.css` which is **not** the current design system.

```
prompt_matrix/templates/
  base.html          # Base template (loads founder_workbench.css)
  index.html         # Old workbench template — its routes (/app, /compose…) were removed; not served
  parsing.html       # Parsing dashboard
  auth.html          # Sign in/up (Clerk integration)
  connect.html       # Provider connection
  ...                # Other pages
```

**Warning:** `founder_workbench.css` is a different design system from `prototype/shell.css`. Pages extending `base.html` get the wrong visual style for the current application.

## Parsing Page (`/parsing`)

**Route:** `GET /parsing` — no auth required (intentional, security deferred)

**Template:** `prompt_matrix/templates/parsing.html`

**Backend:** `prompt_matrix/web.py::parsing_page()`

**What it shows:**
- Summary bar: total documents, JDF CLI count, Textract count, avg parse confidence
- Document cards with parser badges (JDF CLI = green, Textract = amber)
- Per-document: filename, pages, parse/OCR confidence, table/image/figure counts
- Empty state when no documents parsed

**Data flow:**
```
web.py parsing_page()
  → list_substrate_for_project(project_id)
  → builds documents[] and summary{}
  → renders parsing.html with summary=summary, documents=documents
```

**Known bug (fixed):** Template expected `{{ total }}` but `web.py` passed `summary={total: ...}`. Fixed by changing template to use `{{ summary.total }}`, `{{ summary.jdf_count }}`, etc.

**Design system:** Uses `style.css` (v2.0 design tokens) + inline styles matching prototype design principles. Does NOT extend `base.html` (avoids `founder_workbench.css`).

## File Inventory — Where Things Live

### Application Core
| Path | Purpose |
|------|---------|
| `prompt_matrix/web.py` | Flask app factory; routes registered from `prompt_matrix/routers/` |
| `prompt_matrix/services/pdf_ingest.py`, `parser_router.py`, `jdf_converter.py`, `verification.py` | parse pipeline, parser choice, jdf-cli + OCR, Z3 + Red-Hat |
| `prompt_matrix/db/pg_compat.py` | PostgreSQL backend for the SQLite-dialect SQL |
| `prompt_matrix/tasks/` | Celery tasks (parse queue) |
| `prompt_matrix/cloud_auth.py` | Clerk auth (optional, gated by `ASSURE_EDITION`) |
| `prompt_matrix/ui_cache.py` | CSS/JS version strings (`assure-98`) |
| `prompt_matrix/static/` | Shared static files (style.css, script.js, etc.) |

### UI — Current (prototype)
| Path | Purpose |
|------|---------|
| `prototype/index.html` | Main workbench — the actual app |
| `prototype/shell.css` | Current design system |
| `prototype/shell.js` | Current client behavior |

### UI — Legacy (prompt_matrix)
| Path | Purpose |
|------|---------|
| `prompt_matrix/templates/base.html` | Base template (uses founder_workbench.css) |
| `prompt_matrix/templates/parsing.html` | Parsing dashboard |
| `prompt_matrix/static/founder_workbench.css` | Legacy design system (NOT current) |
| `prompt_matrix/static/style.css` | v2.0 design tokens (shared) |

### Configuration
| Path | Purpose |
|------|---------|
| `.env.staging` | Staging environment (EC2) — untracked; copy from `.env.staging.example` |
| `.env.production` | Production environment |
| `prompt_matrix/.env` | Local development |

### Deploy
| Path | Purpose |
|------|---------|
| `docker-compose.yml` | Base stack (PostgreSQL, Redis, web, worker, shell, ollama) |
| `docker-compose.staging.yml`, `docker-compose.prod.yml` | Overlays: real secrets, cloud models, own volumes |
| `infra/terraform/` | AWS: ECS Fargate ARM64, RDS PostgreSQL, ElastiCache, SQS, S3 |

## Branches

| Branch | Purpose |
|--------|---------|
| `main` | Default. Contains prototype UI + prompt_matrix legacy UI. Marketing site at root. |
| `staging` | Staging EC2 deploy. Same as main + staging-specific changes. |
| `webpage` | Cloudflare Worker marketing site. |

**GitHub default branch is `staging`.** `webpage` carries the marketing site.

## Clerks and Auth

- `/parsing` is **not** in `PROTECTED_HTML` — accessible without Clerk session
- `base.html` conditionally renders Account/Sign in based on Clerk auth state
- `ASSURE_EDITION=self-hosted` disables Clerk entirely
- Auth protection is deferred — security is a later concern

## Revisions

When making changes to the parsing page or UI:

1. **Prototype UI changes** → edit `prototype/index.html`, `prototype/shell.css`, `prototype/shell.js`
2. **Parsing page changes** → edit `prompt_matrix/templates/parsing.html`
3. **Route/backend changes** → edit `prompt_matrix/web.py`
4. **Design system changes** → edit `prototype/shell.css` (current) or `prompt_matrix/static/style.css` (shared tokens)

Do NOT use `founder_workbench.css` for new work — it is the legacy system.

## Notes

---

## Tests

```bash
docker compose -f docker-compose.dev.yml up -d
DATABASE_URL=postgresql://assure:assure@localhost:5432/assure .venv/bin/pytest tests/ -q \
  --ignore=tests/e2e --ignore=tests/playwright --ignore=tests/quality_check
```
Every test gets its own PostgreSQL schema; CI runs the same against a PostgreSQL service container plus the Playwright suite.
