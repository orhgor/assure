# Assure — working notes for AI coding sessions

Assure ("The Intellectual Compiler") is a Flask app: upload a source document,
compile a structured **JDF** draft with provenance, verify claims with **Z3**
and a **Red-Hat** critique pass, refine surgically, export. Package:
`prompt_matrix/`. Product name is Assure; the pip name is `prompt-matrix`; the
compile engine is PEM.

## Non-negotiables (read before changing anything)

- **PostgreSQL only.** `DATABASE_URL=postgresql://…` is required; there is no
  SQLite. The repositories still write SQLite-dialect SQL with `?` placeholders
  — `prompt_matrix/db/pg_compat.py` translates it at statement level and keeps
  the `sqlite3.Connection` / `sqlite3.Row` surface (`row[0]`, `row["col"]`,
  `except sqlite3.IntegrityError`). New SQL: keep to that dialect (portable
  subset) and it will run. Don't add SQLite-only tricks the translator does
  not know (check `translate()` first).
- **Nothing on the web tier may parse, OCR or run long Z3.** Uploads are
  staged in the object store and queued (`PARSE_ASYNC=1`,
  `tasks/parse_tasks.py`); `GET /api/tasks/<id>` is the poll endpoint. Routes
  answer 202, not 200, for document ingest.
- **No per-instance state.** Files → `services/object_store.py` (S3 or
  `<ASSURE_DATA_DIR>/objects/`); counters/locks → Redis
  (`services/redis_client.py`); everything else → PostgreSQL. `create_app`
  refuses to start in production/staging without `REDIS_URL` and
  `PEM_SECRET_KEY`.
- **Every upload is an ingest job.** `db/ingest_jobs_repository.py` (schema v27) records
  stage, parser, OCR confidence, Z3 verdict, revision, artifact, error; routes under
  `/api/projects/<id>/ingest-jobs`, panel in `static/ingest_jobs.js`. New pipeline
  steps must call `advance()` (via `_job_advance` in `services/pdf_ingest.py`).
- **One AWS identity, resolved by `services/aws_integration.py`:** `.env` keys →
  credentials saved from the Sources panel (encrypted in `integration_settings`) →
  the task/instance IAM role. Production uses the role; local development uses
  keys against the **real** bucket. Never add a mock S3 path; if the bucket is not
  reachable the UI says so and uploads stay local.
- **Never fabricate confidence or status.** `parse_confidence` /
  `ocr_confidence` are real numbers or `None`; verification reports `skipped`
  / `ERROR`, never an unearned PASS. `docs/anti-claims.md` lists claims the
  code and docs must not make; update it when behaviour changes.
- **Parser routing lives in one place:** `services/parser_router.select_parser`
  → `"jdf"` (text layer) / `"jdf-ocr"` (scan, jdf-cli + tesseract) /
  `"textract"` (only when configured or OCR fails). Callers execute the
  decision; they never add their own branch. A guard test enforces this.
- **Dual imports.** Every module does `try: from ..x import y / except
  ImportError: from x import y` because `prompt_matrix/` is sometimes the
  sys.path root (CLI, systemd, PyInstaller). Follow it in new modules.
- **Docstrings state why, with evidence** (dates, measurements, file refs).
  Match that; no decorative comments.

## Layout

| Path | What |
|---|---|
| `prompt_matrix/web.py` | Flask app factory `create_app`; gunicorn entry `prompt_matrix.web:app`; `register_*_routes(app)` calls near the end |
| `prompt_matrix/routers/` | Route registrars (not blueprints, except `health.py`). `jdf_routes.py` (import-pdf, presign, JDF save/history), `substrate.py` (Sources vault), `jdf_memory_routes.py` (`/jdf/ingest`), `async_tasks_routes.py` (`/api/tasks/<id>`), `draft.py` / `inquire_stream.py` (SSE compile) |
| `prompt_matrix/services/` | Business logic. `pdf_ingest.py` (the parse pipeline, shared by route and worker), `parser_router.py`, `jdf_converter.py` (jdf-cli + OCR), `verification.py` (Z3 + Red-Hat hook), `omp.py` (artifacts), `object_store.py`, `redis_client.py` |
| `prompt_matrix/db/` | `pg_compat.py` (PostgreSQL backend), `connection.py` (migrations, `_SCHEMA_VERSION`), `pool.py` (facade), `*_repository.py` (plain functions) |
| `prompt_matrix/history.py` | `get_db()` (request-scoped) / `db_scope()` (standalone unit of work) / `borrowed_connection()`. Always use these; never open connections yourself |
| `prompt_matrix/tasks/` | Celery: `parse_tasks.py` (queue `parse`), `substrate_tasks.py`, `redhat.py`, `compile_tasks.py`, `llm_tasks.py` |
| `prompt_matrix/celery_app.py` | Broker: `CELERY_BROKER_URL` → `REDIS_URL` → `sqs://`; results follow Redis or PostgreSQL |
| `prompt_matrix/models/jdf.py`, `models/omp.py` | Pydantic document tree / artifact models |
| `prompt_matrix/static/`, `templates/` | Vanilla JS + Jinja; bump `ui_cache.py` for cache-busting; all UI strings via `i18n.py` catalogs (7 locales, update all in the same change) |
| `tests/` | pytest; runs against PostgreSQL (`docker compose -f docker-compose.dev.yml up -d`) |
| `infra/terraform/` | AWS stack: ECS Fargate (ARM64) web + Spot worker, RDS PostgreSQL, ElastiCache, SQS, S3, ALB |
| `docs/` | Read first: `scale_architecture.md`, `decisions.md`, `anti-claims.md`, `deferred.md`, `deploy-flow.md`, `deferred-parse-runtime.md` |
| `scripts/migrate_sqlite_to_postgres.py` | One-time import of a legacy SQLite file |
| `prototype/` | Prototype shell (`dev-server.py` :8891 gate + `index.html`/`shell.js`, proxies `/api/*` to Flask). **Not the UI we ship**: the product UI is Flask's `/app` workbench (`templates/` + `static/`), the one CI's Playwright suite tests via `tests/e2e/conftest.py::live_assure_server`. The shell is behind `docker compose --profile shell` |
| `worker/`, `landing/`, `openuser/`, `scripts/aws/_probe_*` | Cloudflare edge worker (being retired), marketing site, UX runner, throwaway probes — don't extend |

## Document pipeline

`bytes → parser_router.select_parser → jdf_converter.pdf_to_parse_bundle`
(`jdf convert [--ocr tesseract]` + `jdf chunk`) or `lib/textract` →
`jdf_to_document_tree` → `verification.run_verification_after_parse` (Z3 +
Red-Hat, never raises) → `db/jdf_repository.save_jdf_revision` → `omp.
store_omp_artifact` (row + S3/local JSON mirror). All of it in
`services/pdf_ingest.ingest_pdf_for_project`, called by the worker task or,
with `PARSE_ASYNC=0`, inline by the route.

jdf-cli is pinned (`@uurtech/jdf-cli@0.2.3`, Dockerfile `JDF_CLI_VERSION`);
its tesseract language data is baked into the image (`TESSERACT_LANGS`).
Measured: text-layer 10 pages ≈ 1 s; scanned ≈ 3 s/page/vCPU.

## Running

```bash
uv sync --extra dev                                   # Python 3.11 venv (.venv)
docker compose -f docker-compose.dev.yml up -d        # PostgreSQL :5432 + Redis :6379
export DATABASE_URL=postgresql://assure:assure@localhost:5432/assure
export REDIS_URL=redis://localhost:6379/0
.venv/bin/python -m prompt_matrix.web --web --port 8765   # or: assure --web → http://127.0.0.1:8765/app (how CI runs it)

# full stack in containers (same topology as AWS; APP_IMAGE, POSTGRES_PASSWORD in .env)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Key env: `DATABASE_URL`, `REDIS_URL`, `ASSURE_DATA_DIR`, `ASSURE_S3_BUCKET`,
`CELERY_BROKER_URL`, `PARSE_ASYNC`, `PARSER_SCAN_BACKEND` (`jdf-ocr|textract`),
`JDF_OCR`, `PEM_SECRET_KEY`, `ENVIRONMENT`, `PROXY_FIX_HOPS`,
`ASSURE_LOG_STDOUT`. Full list with meanings: `.env.example`.

## Tests

```bash
DATABASE_URL=postgresql://assure:assure@localhost:5432/assure \
.venv/bin/pytest tests/ --ignore=tests/e2e --ignore=tests/playwright --ignore=tests/quality_check \
  --ignore=tests/test_sanitization.py --ignore=tests/test_confidence_overlay.py \
  --ignore=tests/test_full_audit.py --ignore=tests/test_z3_benchmark.py \
  --ignore=tests/test_z3_explanation.py --ignore=tests/test_inquire_stream.py -q
```

Each test that sets `DATABASE_PATH` gets its own PostgreSQL **schema** (derived
from the path); `conftest.py` drops them at session end and shims legacy
`sqlite3.connect(path)` calls onto that schema. Celery is eager under test;
set `PARSE_ASYNC=1` in a test to exercise the 202/task path. The Z3-heavy
modules are run one by one in CI because Z3 can segfault (exit 139 tolerated).
Known pre-existing failures (i18n `counter.*` keys, founder-workspace
fixtures, a few stale expectations) are listed in the product audit of
2026-09-22; don't "fix" them by loosening assertions.

## Deploy

GitHub Actions builds `ghcr.io/orhgor/assure-app:<sha>` (linux/arm64);
`infra/terraform` runs it on ECS. Single-host EC2 still works with the compose
files. Do not `docker build` on EC2; do not commit `.env*`.

## Conventions

- Routes return `jsonify({"ok": False, "error": ...}), <status>`; long work
  returns 202 + `task_id`.
- Repository functions: `init_db()` first, then `get_db()` / `db_scope()`;
  `INSERT … ON CONFLICT` for anything a retry could insert twice; qualify
  columns in `DO UPDATE SET x = table.x + 1`.
- Env parsing idiom: `os.environ.get("X", "").strip().lower() in ("1","true","yes")`.
- Commits: `feat(scope):`, `fix(scope):`, `docs(scope):`, `test:`.
