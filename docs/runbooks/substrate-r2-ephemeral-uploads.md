# Substrate uploads — ephemeral R2 staging (design)

**Status:** Design only — no implementation yet.
**Constraint:** Raw PDFs/documents are **never** long-term assets on EC2 disk or in the marketing R2 bucket. Processing (Textract/Docling, validation, SQLite) stays on **EC2**. R2 holds bytes **only until extraction completes**, then objects are **deleted**.

---

## Goals

1. Upload path does not fill EC2 `/` (today: in-memory or `data/tmp_uploads/` on box).
2. Raw files are not retained after successful ingest — only **extracted text**, tables, and forms in SQLite (`substrate_vault`), matching current product behavior.
3. Failed or abandoned uploads are garbage-collected (immediate delete + bucket lifecycle).
4. Separate from **marketing** R2 (`assure-marketing-prod`) and from **SQLite backup** R2 (`assure-prod-backups`).

---

## Buckets (do not mix)

| Bucket | Purpose | Lifetime |
| --- | --- | --- |
| `assure-marketing-prod` | Static HTML/CSS/JS | Until next deploy |
| `assure-prod-backups` | Compressed SQLite backups | Retention policy (days) |
| **`assure-uploads-staging`** (new) | Transient PDF/image uploads | **Minutes**; max 24 h via lifecycle rule |

Staging EC2: `assure-uploads-staging-staging` (or prefix `staging/` in one bucket — prefer **separate bucket** to avoid cross-env leaks).

Credentials: EC2 only — `/etc/assure/backup.env`-style `R2_*` vars or dedicated `/etc/assure/uploads.env`. Never in marketing Worker or static build.

---

## Split of responsibility

| Layer | Role |
| --- | --- |
| **Browser** | Upload via workbench UI (`/app` on EC2). Optional later: presigned PUT direct to R2. |
| **EC2 `/api/projects/<id>/substrate/*`** | Auth (Clerk), validate limits, write/read/delete staging objects, run Textract/Docling, persist extraction, **delete R2 object**. |
| **R2 staging bucket** | Dumb object store — no Worker, no public GET. S3-compatible API from EC2 (same pattern as `scripts/aws/backup-sqlite.sh`). |
| **SQLite** | Long-term: `extracted_text`, `tables_json`, `forms_json`, metadata — **not** raw PDF bytes. |

---

## Object key layout

```
uploads/{env}/{user_id}/{project_id}/{upload_id}/{secure_filename}
```

- `upload_id`: UUID v4, generated at start of upload session.
- `secure_filename`: werkzeug `secure_filename` (existing).
- No predictable keys; no listing without server-side prefix + auth.

---

## Upload flows

### Flow A — EC2 relay (recommended v1)

Minimal change; fixes EC2 disk pressure.

1. `POST /api/projects/<project_id>/substrate/upload` (existing route, Clerk + project ownership).
2. EC2 validates `validate_upload_bytes()` (10 MB, 50 pages, MIME) **before** R2 write.
3. Stream bytes to R2 staging (`PutObject`).
4. EC2 reads back from R2 (or keeps validated bytes in memory for small files — optional optimization).
5. `ingest_substrate_file()` — Textract/Docling (unchanged).
6. On SQLite commit success → **`DeleteObject`** on R2 key.
7. On extraction failure → delete R2 key; return 400/503; no raw file retained.
8. Response unchanged for client (`id`, `text`, `page_count`, …).

Remove reliance on persistent `data/tmp_uploads/` except as optional local buffer; Celery async path reads from R2 key instead of local path.

### Flow B — Presigned direct upload (v2)

For large files / lower EC2 memory.

1. `POST …/substrate/upload/init` → returns `{ upload_id, presigned_put_url, expires_in }`.
2. Browser `PUT` file to R2 (Clerk session required; URL short-lived, e.g. 15 min).
3. `POST …/substrate/upload/complete` with `{ upload_id }` → EC2 verifies object exists, size, content-type → extract → delete R2.
4. Orphan objects: lifecycle rule deletes keys under `uploads/` older than 24 h without `complete`.

---

## Delete / erase policy

| Event | Action |
| --- | --- |
| **Extract success** | Delete R2 object immediately (in same request/task, before HTTP 200). |
| **Extract failure** | Delete R2 object; log audit `SUBSTRATE_UPLOAD` failure. |
| **User DELETE** `/substrate/<file_id>` | Delete SQLite row only (no R2 object — already gone after ingest). |
| **Upload never completed** (presigned v2) | Lifecycle: expire after **24 hours**. |
| **Celery task crash** | Task retry reads R2 by key; `finally` block deletes R2 + any temp file. |

Optional env `SUBSTRATE_RETAIN_R2=1` for debug/staging only — **off in production**.

---

## Schema addition (future)

Add nullable column to `substrate_vault`:

```sql
r2_staging_key TEXT  -- set during processing; NULL after delete
processing_status TEXT  -- queued | extracting | ready | failed
```

Used for async jobs and idempotent cleanup. Not exposed to client. Never store raw bytes in SQLite.

---

## Security

- All upload APIs stay on **EC2** behind Clerk + `project_ownership_required` (existing).
- R2 bucket: **private**; no public access; no Worker routes.
- Presigned URLs (v2): single-object, short TTL, content-length / content-type conditions where supported.
- Same limits: 10 MB, 50 pages, PDF/image only (`upload_limits.py`).
- Audit log: `SUBSTRATE_UPLOAD` with `r2_key` hash or upload_id (not full user path in client logs).

---

## EC2 implementation notes (future PR)

1. **`prompt_matrix/lib/r2_staging.py`** — put/get/delete using `boto3` + R2 endpoint (mirror backup script env vars: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_UPLOADS_BUCKET`).
2. **`routers/substrate.py`** — wire Flow A; replace `_temp_upload_dir()` persistence with R2 key in Celery args.
3. **`tasks/substrate_tasks.py`** — `process_substrate_upload(project_id, r2_key, filename)`; delete R2 in `finally`.
4. **IAM on R2 token** — minimal: `PutObject`, `GetObject`, `DeleteObject`, `HeadObject` on `assure-uploads-staging` only.
5. **Wrangler / lifecycle** — one-time bucket create + lifecycle rule (CLI or dashboard); document in runbook.

---

## What does not change

- Marketing R2 / Worker — no document uploads on apex.
- Long-term evidence is **extracted text** in SQLite + JDF provenance — not file replay.
- Textract runs from EC2 (AWS credentials on box, existing).
- Open-source / provider integrations remain EC2-only.

---

## Implementation phases

| Phase | Deliverable |
| --- | --- |
| **P0** | Create `assure-uploads-staging` bucket + lifecycle + EC2 credentials |
| **P1** | Flow A: relay upload → R2 → extract → delete; remove durable `tmp_uploads` |
| **P2** | Async Celery path uses R2 key; tests with moto or minio |
| **P3** | Flow B presigned (optional) |
| **P4** | Metrics: staging object count, orphan age alerts |

---

## Related

- Current ingest: `prompt_matrix/routers/substrate.py`, `tasks/substrate_tasks.py`
- Limits: `prompt_matrix/upload_limits.py`
- R2 backup pattern: `scripts/aws/backup-sqlite.sh`
- Stack split: `.cursor/rules/deploy-flow.mdc`
