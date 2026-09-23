# Scale architecture

Status: implemented 2026-09-22 (code + Terraform), superseding the "described,
not built" version of this document. The same code runs on one laptop with
`docker compose` and on AWS with `infra/terraform`; only the broker differs.

## 1. Topology

```
                 browser
                    │  PUT (presigned)            POST /import-pdf {object_key}
                    ├──────────────────► S3 ◄────────────────────┐
                    │                    ▲  get/delete            │
                    ▼                    │                        │
   ALB ──► web (ECS Fargate, N replicas) ┼──► SQS `parse` ──► worker (Fargate On-Demand, 0..M)
              │        │                 │                          │
              │        └── Redis ────────┼──────────────────────────┤  rate limits, debounce,
              │        (ElastiCache)     │                          │  task results
              └────────── PostgreSQL (RDS) ◄────────────────────────┘  revisions, vault, OMP rows,
                                                                        audit, budgets, locks
```

| Concern | Where it lives | Why |
|---|---|---|
| Metadata, revisions, vault rows, OMP artifact rows, audit, quotas, locks | **PostgreSQL** (`DATABASE_URL`) | Multi-writer, row locks, FK cascades. SQLite is gone (`db/pg_compat.py` runs the existing SQL unchanged). |
| Uploaded documents, OMP artifact JSON, JDF tree mirror | **S3** (`ASSURE_S3_BUCKET`) | Bytes must not sit on one instance's disk; workers fetch by key. Local: `<ASSURE_DATA_DIR>/objects/`. |
| Parse queue | **SQS** on AWS, **Redis** locally (`CELERY_BROKER_URL`) | SQS: zero idle cost, DLQ, native queue-depth metric for autoscaling. |
| Rate limits, Red-Hat debounce, task results | **Redis** (`REDIS_URL`) | Counters and short locks shared by every replica. |
| Parse + OCR + Z3 + Red-Hat | **worker tier** (`tasks/parse_tasks.py`) | CPU-bound and minutes long on scans; never on a request thread. |

## 2. Request path for a document

1. `POST /api/projects/<id>/uploads/presign` → `{mode:"s3", object_key, upload:{url}}`
   (or `mode:"multipart"` without S3). Browser PUTs the file straight to S3.
2. `POST /api/projects/<id>/import-pdf` with `{object_key, filename}` (or the
   multipart file, which the web replica only stages) → **202**
   `{task_id, status_url}`. The web tier never parses.
3. `GET /api/tasks/<task_id>` → pending / processing / success / failure /
   skipped, answered by any replica from the Celery result backend.
4. Worker (`assure.import_project_pdf`): fetch bytes → `parser_router.select_parser`
   → `jdf-cli` (text layer) / `jdf-cli --ocr tesseract` (scan) / Textract
   (only when configured or when OCR fails) → tree → Z3 + Red-Hat →
   `save_jdf_revision` → OMP artifact row + S3 mirror → delete staged object.
   Redelivery after a crash finds the object gone and reports `skipped`.

Measured on the containerized stack (linux/arm64, 2 vCPU): text-layer 10 pages
**1.1 s** end to end on the worker; 3 scanned pages with tesseract **8.1 s**,
OCR confidence 0.89. Standalone `jdf convert --ocr tesseract` for 10 scanned
pages: **28 s** at 2 vCPU, 33 s at 1 vCPU (tesseract is single-threaded per
page). Budget ~3 s per scanned page per worker vCPU; a t4g/Graviton2 core is
slower than the Apple-silicon container these were measured in, plan on 2×.

## 2a. Watching a document: ingest jobs

Every upload creates an `ingest_jobs` row (schema v27) before the message is
queued, and the worker advances it: `queued → fetching → parsing → verifying →
persisting → done` (or `failed` / `skipped`), recording the parser that took the
document, page count, OCR confidence, the Z3 verdict with its violation count,
Red-Hat status, revision and OMP artifact ids, per-stage timestamps, duration
and the error text on failure. The row outlives the Celery result.

| Surface | What it shows |
|---|---|
| `GET /api/projects/<id>/ingest-jobs[?status=active]` | list + per-status counts + Z3 tally |
| `GET /api/projects/<id>/ingest-jobs/<job>` | one job with its stage history |
| `GET /api/projects/<id>/ingest-jobs/<job>/report` | job + the stored revision's `meta.z3` (violations), Red-Hat finding count, OMP artifact |
| `POST /api/projects/<id>/ingest-jobs/<job>/retry` | re-queue a failed job while its staged object exists (409 otherwise) |
| `GET /api/tasks/<task_id>` | Celery status joined with the job; the job wins once terminal |
| Workbench → Sources → **Processing** panel (`static/ingest_jobs.js`) | live stage bar, parser/OCR/Z3 badges, error + Retry, Report drawer; polls while anything is active |

`TIMEOUT` / `ERROR` from Z3 are shown as *unverified*, never as a pass.

## 2b. Credentials

`boto3` (S3, SQS, Textract, Bedrock) resolves one identity for web and worker,
in this order (`services/aws_integration.py`):

1. `.env` — `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`,
   `AWS_DEFAULT_REGION`, `ASSURE_S3_BUCKET`.
2. Credentials entered on the Sources panel (`PUT /api/integrations/aws`),
   stored in `integration_settings` with the secret Fernet-encrypted
   (`ENCRYPTION_KEY`, or a key derived from `PEM_SECRET_KEY` locally), applied
   to the process environment at web and worker start.
3. The machine role — ECS task role (`infra/terraform/ecs.tf`) or the EC2
   instance profile (`scripts/aws/attach-runtime-role.sh`). The production shape.

`GET /api/integrations/aws` performs a real `HeadBucket` + `GetCallerIdentity`
and the Sources panel shows the result: a warning when no bucket is configured
(uploads stay on the instance disk), an error with the reason when the bucket
is unreachable, a quiet status line when connected. Nothing is mocked: running
locally means running against the real bucket with real credentials.

## 3. Scaling rules

| Tier | Metric | Range |
|---|---|---|
| web | ECS CPU 60 %, ALB 400 req/target | 2–6 × 0.5 vCPU / 1 GB |
| worker | SQS `parse` visible messages (step scaling), idle 10 min → min | 0–10 × 1 vCPU / 2 GB, Fargate On-Demand (no Spot — user decision 2026-09-22; `worker_use_spot` exists but stays false) |
| PostgreSQL | manual class change; storage autoscaling | db.t4g.micro → small → medium; Aurora Serverless v2 when needed |
| Redis | — | cache.t4g.micro |

Idempotency: `task_acks_late` + `task_reject_on_worker_lost`; staged object keys
carry a UUID; `INSERT … ON CONFLICT` everywhere a redelivery could insert twice.

## 4. What the web tier must never do

Parse a document, run OCR, call Textract, run Z3 on a whole document, write a
file it expects to read back, hold state in a module-level dict. Everything on
that list has a home above. `web.create_app` refuses to start in
production/staging without `REDIS_URL` and `PEM_SECRET_KEY`; the first query
refuses to run without a PostgreSQL `DATABASE_URL`.

## 5. Cost controls

| Resource | Control |
|---|---|
| Textract | Off by default: scans go to jdf-cli's bundled tesseract (`PARSER_SCAN_BACKEND=jdf-ocr`). Textract only as the fallback when OCR fails, or when explicitly configured. |
| Worker compute | Scale to zero; On-Demand only (Spot is not used); one vCPU per concurrency slot. |
| Z3 | `Z3_SOLVER_TIMEOUT_MS` (30 s) per solver call, on the worker. |
| S3 | `uploads/` expire after 1 day; artifacts stay in S3 Standard (no tiering). |
| NAT | Single NAT gateway; S3/ECR/logs/Secrets/SQS through VPC endpoints. |

## 6. Cloudflare R2 → S3 (done 2026-09-23)

The edge worker (`worker/index.js`: R2 staging, unpdf, Textract fallback,
POST to `/api/substrate`) was replaced by the presigned S3 flow above and
deleted on 2026-09-23 (`worker/`, its deploy workflow, the `/api/substrate`
edge route and `SUBSTRATE_INGEST_SECRET`). What remains outside the repo:

1. Deploy this stack; `ASSURE_S3_BUCKET` set, `PARSE_ASYNC=1`.
2. Switch the upload UI to `uploads/presign` → PUT → `import-pdf {object_key}`
   (multipart keeps working meanwhile).
3. Leave `SUBSTRATE_INGEST_SECRET` / `ASSURE_EDGE_WORKER_URL` unset; the
   `/api/substrate` route then rejects edge posts.
4. Delete the Cloudflare worker and the two R2 buckets. Database backups are
   RDS snapshots; the SQLite→R2 backup cron scripts (`backup-sqlite.sh`,
   `setup-sqlite-backup.sh`, `setup-r2-backup-full.sh`) were removed 2026-09-23.

Existing data: `scripts/migrate_sqlite_to_postgres.py <history.sqlite>` copies
every table into PostgreSQL (tested; identity sequences reset).

## 7. Local

```bash
docker compose -f docker-compose.dev.yml up -d          # PostgreSQL + Redis for the venv
DATABASE_URL=postgresql://assure:assure@localhost:5432/assure .venv/bin/pytest -q

docker compose up -d --build                             # full stack, same topology as AWS
```

## 8. Still open

- Async verification for >50-page documents (`TODO(async-verification)` in
  `services/verification.py`, `docs/deferred.md`).
- Turkish OCR: `jdf convert --ocr` is English-only; other languages need a
  `jdf describe --ocr-language tur` pass after convert.
- SSE compile streams are still served by the replica that accepted them
  (ALB idle timeout is 180 s; no resumable stream id yet).
