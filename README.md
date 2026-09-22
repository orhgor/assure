# Assure

Ask one question. Get one verified answer. The question is rewritten for the model you pick, then checked.

The compiler is PEM (`prompt_matrix`). `assure --web` opens your browser. First run: paste a provider key, then write a question. There is no password prompt on this computer unless you set one.

## Quick start

The database is PostgreSQL and the shared state is Redis; both come from one
compose file. The app runs from the venv or as a container.

```bash
./scripts/install.sh                                    # or: uv sync --extra dev
docker compose -f docker-compose.dev.yml up -d          # PostgreSQL :5432 + Redis :6379
export DATABASE_URL=postgresql://assure:assure@localhost:5432/assure
export REDIS_URL=redis://localhost:6379/0
.venv/bin/assure --web                                  # http://127.0.0.1:8765
```

Full stack in containers (web + parse worker + PostgreSQL + Redis, the same
topology as AWS) with no configuration at all:

```bash
docker compose up -d --build          # UI: http://127.0.0.1:8891 (key: assure-local-shell-key) · API: http://127.0.0.1:8765
```

Optional `.env` at the repo root: provider keys, and `ASSURE_S3_BUCKET` +
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` for a real S3 bucket (or enter
them on the Sources panel). Production: `docker-compose.prod.yml` requires
`APP_IMAGE`, `POSTGRES_PASSWORD`, `PEM_SECRET_KEY`.

Documents are parsed on the worker: `POST /api/projects/<id>/import-pdf`
answers 202 with a `task_id`, `GET /api/tasks/<task_id>` reports the result.
Scanned PDFs are OCR'd by jdf-cli's bundled tesseract; Textract is only a
fallback. Architecture, scaling rules and the AWS Terraform:
[docs/scale_architecture.md](docs/scale_architecture.md), [infra/terraform](infra/terraform/README.md).

Desktop build: `./scripts/build-desktop.sh` (needs a reachable PostgreSQL via `DATABASE_URL`).

Migrating a legacy SQLite file: `DATABASE_URL=... python scripts/migrate_sqlite_to_postgres.py data/history.sqlite`.

Install, CLI, and MCP details: [prompt_matrix/README.md](prompt_matrix/README.md). Product overview: [prompt_matrix/PEM.md](prompt_matrix/PEM.md). Public landing: [landing/index.html](landing/index.html).

## Testing

**CI** runs `pytest` against a PostgreSQL service container plus a **Playwright** suite under `tests/playwright/` (headless Chromium, embedded Flask — no live model keys).

```bash
docker compose -f docker-compose.dev.yml up -d
DATABASE_URL=postgresql://assure:assure@localhost:5432/assure .venv/bin/pytest tests/ -q \
  --ignore=tests/e2e --ignore=tests/playwright --ignore=tests/quality_check
```

Every test gets its own PostgreSQL schema; they are dropped when the session ends.

## Cloudflare

Two branches, two surfaces:

| Branch | Deploy target | URL |
|--------|---------------|-----|
| `p4-account-wallet` | EC2 Docker — image built in **GitHub Actions**, pulled on EC2 ([deploy flow](docs/deploy-flow.md)) | [getassureai.com](https://getassureai.com) |
| `webpage` | Cloudflare Worker `assure` — 301 → app host | [getassureai.com](https://getassureai.com) → app |

GitHub [`orhgor/assure`](https://github.com/orhgor/assure) default branch is **`webpage`** (marketing site at repo root). The JDF Workstation and PEM engine live on **`p4-account-wallet`** and deploy to EC2 — not through Cloudflare Workers Builds.

Cloudflare Workers Builds for Worker **`assure`** must connect to **`webpage` only**. A red **Workers Builds: assure** check on an app PR is irrelevant (wrong branch / missing root `wrangler.jsonc`). Fix: [docs/cloudflare-fix.md](docs/cloudflare-fix.md) or `bash scripts/cloudflare/set_workers_branch.sh`.

Marketing deploy: copy `landing/` to a webpage checkout, then push `webpage`:

```bash
./scripts/sync-webpage.sh /path/to/webpage-checkout
```

Worker deploy command: `npx wrangler deploy`. `wrangler.jsonc` must list `assets.directory` (not a Pages `pages_build_output_dir`).
