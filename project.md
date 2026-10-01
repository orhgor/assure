# Assure — build brief (rewrite from scratch)

You are building **Assure** from an empty repository. This document is the whole
brief: what the product does, what the finished system must look like, and the
rules it must never break. Where this brief and any older code or docs disagree,
this brief wins. Do not port code from the previous implementation. It had too
many parallel paths, and they produced logic errors (see §12).

---

## 1. What Assure is

Assure turns **uploaded documents** into **verified, structured information** and
writes **documents whose every claim is checked against those sources**.

It has two halves:

1. **Intake (Parsure).** The user uploads a PDF or an image: a scan, a phone
   photo, a screenshot, or a digital PDF. **Claude Opus 5.5 on Amazon Bedrock**
   reads it page by page, including handwriting. Assure stores that reading
   (the *analysis*) and shows it on the page: the original as it looks, with
   fields and tables drawn where they sit. It then turns the analysis into typed
   **fields**, each with a value, a location on the page, a status, and a review
   trail.
2. **Compile and verify (Assure).** The user asks for a document, for example
   "summarise this claim" or "draft the coverage letter". **Claude Sonnet 5.5**
   writes a structured draft from the project's sources. Assure then verifies
   every claim in it:
   - **Anchoring:** is there a source passage for the claim?
   - **Entailment:** does that passage support it?
   - **Numeral audit:** do the numbers reconcile? This runs in **Z3**.
   - **Red-Hat:** an adversarial critique pass.

   The user can then fix any part surgically and export the result with its
   verification evidence.

**One-line test of success:** upload a scanned, handwritten form. Within seconds
to a minute you should see:
- the page itself, with every field boxed and every table outlined;
- a panel listing the fields and tables Opus read, with handwriting marked;
- the fields in a review list where you can accept, correct, or dispute each one.

Then ask for a summary and get a draft in which every sentence links back to the
box it came from.

---

## 2. The finished product: what the user sees and can do

### 2.1 Projects and sources
- A **project** is a workspace that holds sources, fields, drafts, and history.
- **Upload:** drag and drop or pick a file.
  - Accepted: PDF, PNG, JPG/JPEG, TIFF, BMP.
  - The size limit is configurable (default 25 MB) and so is the page limit
    (default 200).
  - The upload returns at once with **202 + task id**. The source row then shows
    live stages: `queued → reading → analysing → fields → verifying → done`, or
    `failed` with the real error text.
- The **source list** shows, for each source:
  - file name, page count and type;
  - status;
  - field counts: how many fields, and how many need review;
  - the model that read it, for example "Opus 5.5".

### 2.2 Source view (the core screen)
Opening a source shows **the original document as it looks**.
- **Digital PDF:** the PDF itself, rendered natively. Under it sits the
  **reading panel**:
  - a fields table: label, value, page, and handwritten/illegible markers;
  - every table Opus read, rendered as a real HTML table;
  - the page text, collapsible.
- **Scan, photo, or image:** each page as an image, with overlays drawn from the
  analysis:
  - a **box on every field value**, with a hover or label showing the key;
  - an **outline on every table**;
  - optionally, line boxes;
  - the same reading panel underneath.
- Clicking a field in the panel highlights its box, and clicking a box selects its
  field.
- Handwritten values are visibly marked. Illegible ones show `[illegible]` and are
  never guessed.

### 2.3 Fields and review
- For each source there is a list of **fields** that Opus found. Each one has:
  - a label and a typed value (text, number, date, money, checkbox, signature);
  - its page and bounding box;
  - flags: handwritten, illegible;
  - a status: `accepted | needs_review | disputed | rejected | corrected`.
- Actions: **accept**, **correct** (enter a new value; the old one stays in
  history), and **dispute** (add a reason; a dispute is due in 72 h), then
  **resolve**.
- Every change goes into an **audit log**: who, when, old value, new value, and
  reason.
- **Document type:** Opus proposes one, for example insurance claim, medical
  claim form, invoice, contract, ID, or "other". The user can override it.
- **Optional schemas.** A document type may declare expected fields, such as
  policy_number, insured_name, or premium.
  - A schema field Opus did not find is shown as **absent**, never invented.
  - Extra fields Opus found are kept as **discovered fields**. A schema must
    never hide them.
- **Export:** fields as CSV (long and wide) and JSON, per source and per project.

### 2.4 Compile (drafting)
- The user writes an **intent** and chooses which sources and fields to use.
- Sonnet 5.5 writes a **structured draft**: sections, paragraphs and claims.
  - Every claim carries **citations**: source id, page, and field id or text span
    with bbox.
  - Output is **streamed** (SSE), so the draft appears as it is written.
- The draft is **versioned**. Each compile or edit creates a new revision, and the
  history is browsable with a diff.

### 2.5 Verification
For every claim in a draft:
- **Anchoring:** the cited span exists in the source, and the quoted text is found
  there verbatim or after normalisation.
- **Entailment** (Sonnet 5.5): `supported | partial | contradicted | not_confirmed
  | no_source`, with the model's one-line reason.
- **Numeral audit** (Z3):
  - The numbers in the claims and the source fields are turned into
    constraints, such as totals, sums, and date order.
  - The result is `PASS | FAIL (with the violated constraint) | TIMEOUT | ERROR |
    SKIPPED`.
  - A timeout or error is **never** shown as pass.
  - Z3 runs on the worker, with a time limit.
- **Red-Hat** (Sonnet 5.5): an adversarial reviewer lists the weakest claims and
  why. Its findings attach to claims.
- The UI shows a status chip per claim and an overall verdict per draft: verified
  / review required / not verified.

### 2.6 Surgical edit
- The user selects a paragraph or claim and asks for a change, for example
  "shorter" or "fix the date".
- Only that node is rewritten. The rest of the draft is untouched, and the
  rewritten node is re-verified.
- The user can also select text in the source view and ask a question about it
  ("ask about this").

### 2.7 Compare
- The same intent can run against a second model for comparison. The default
  second model is also Sonnet 5.5, and it can be configured.

### 2.8 Export
- A **Verification Dossier** in PDF and JSON contains:
  - the draft;
  - each claim with its citation and verdict;
  - the Z3 results;
  - the Red-Hat findings;
  - the field review summary;
  - the audit trail.
- If no PDF renderer is available, return 503 with a clear message. Never return
  a broken file.

### 2.9 Accounts (simple)
- Modes: `off` (single user, no login), `local` (accounts in PostgreSQL), or
  `clerk` (optional).
- Roles: owner, reviewer, auditor (read-only).
- Every `/api` call is checked for project ownership when login is on.
- A shell gate password (`SHELL_ACCESS_KEY`) protects the whole site when login is
  off.

### 2.10 Health and operations
- `GET /health` reports:
  - database, redis, object store and worker;
  - Bedrock: models configured, and auth = api_key;
  - PDF renderer.
- Logs are structured JSON on stdout. Every model call is logged with model id,
  tokens, latency, cost estimate, and project/job id.
- There is a **monthly spend cap** for Bedrock, enforced before each call. When
  the cap is reached, the call fails with a clear error and no silent fallback.

---

## 3. The intake pipeline (the part that must be exactly right)

```
upload
  → store original in object store (S3 or local dir)          [web tier, fast]
  → create ingest_job (stage=queued), enqueue task, return 202
worker:
  → render pages
       PDF   : each page → JPEG, long side 2000 px, quality 90 (drop to 75 if
               the request would exceed Bedrock's image size limit)
       image : the image itself (re-encode BMP/TIFF → PNG/JPEG); one page
  → send to Opus 5.5 on Bedrock, N pages per request (default 8), requests
    in parallel (default 4), each page preceded by a "Page n:" text block
  → parse Opus's JSON answer per page → validate against the analysis schema
  → store analysis.json (the raw, validated reading) next to the original
  → derive fields + tables + page text from analysis (pure function)
  → classify document type (from Opus's answer)
  → save fields, page texts, tables in PostgreSQL
  → run verification of intake (Z3 numeric checks on fields, Red-Hat on fields)
  → job stage=done (or failed with the error)
```

**Rules:**
- **There is one parser, and it is Opus 5.5 on Bedrock.**
  - No jdf-cli, no tesseract, no Textract, no PyMuPDF text extraction as a
    "parser", no OCR fallback, no "auto" routing.
  - If Bedrock fails, the job fails and shows the error.
  - PyMuPDF is used **only to rasterise pages** and to count them.
- **Digital PDFs also go to Opus as page images.** Do not send the PDF as a
  `document` block. Measured on 2026-10-01: a scanned PDF sent that way came back
  with no fields. Reading the image is the point.
- **The analysis is the single source of truth.** The fields, the tables, the
  overlays, and the page text the compiler cites all come from `analysis.json`.
  Nothing else writes field values except the user's corrections.
- **Bounding boxes are Opus's estimates.** They are normalised 0–1 `{x,y,w,h}` on
  the page image and are labelled `bbox_source: "model_estimate"`. Do not present
  them as measured coordinates.
- **Confidence is never invented.**
  - If Opus states a confidence, store it as Opus's.
  - Otherwise confidence is `null` and the UI shows "—".
  - There are no default 0.9 values and no "quality scores" computed from
    nothing.
- A truncated answer (max_tokens reached) marks those pages `truncated: true`, and
  the UI says so. Retry once with fewer pages per request before marking them.

### 3.1 Opus prompt contract
The system prompt tells Opus that it is a document reader. It must:
- transcribe everything printed and handwritten;
- never guess: write `[illegible]` instead;
- return **only JSON** in this shape, one object per page it was shown:

```json
{
  "pages": [
    {
      "page": 1,
      "document_type": "medical_claim_form",
      "lines":  [{"text": "...", "bbox": {"x":0,"y":0,"w":0,"h":0}, "handwritten": false, "illegible": false}],
      "fields": [{"key": "Patient name", "value": "Jane Roe",
                  "bbox": {...}, "key_bbox": {...},
                  "kind": "text|number|date|money|checkbox|signature",
                  "handwritten": true, "illegible": false}],
      "tables": [{"headers": ["..."], "rows": [["..."]], "bbox": {...}}]
    }
  ]
}
```

- Checkboxes: the value is `checked` or `unchecked`.
- Signatures: the value is `signed` or `unsigned`, with a bbox.
- Do not send a `temperature` parameter. Opus 5.5 and Sonnet 5.5 on Bedrock reject
  it with a ValidationException.
- The prompt has a version string (for example `parse-v1`) that is stored with
  every analysis.

### 3.2 Stored analysis (`analysis.json`)
```json
{
  "model": "us.anthropic.claude-opus-5-5",
  "prompt_version": "parse-v1",
  "bbox_source": "model_estimate",
  "read_at": "2026-10-01T12:00:00Z",
  "pages": [{ "page": 1, "width_px": 1414, "height_px": 2000,
              "lines": [...], "fields": [...], "tables": [...], "truncated": false }]
}
```
- Stored at `projects/<project>/sources/<source>/rev-<n>/analysis.json`, next to
  `original.<ext>` and `page-<n>.jpg`.
- Served by `GET /api/projects/<p>/sources/<s>/analysis`.

---

## 4. Models and credentials

| Use | Model | Notes |
|---|---|---|
| Reading uploads | Claude Opus 5.5 (`anthropic.claude-opus-5-5`) | Bedrock `invoke_model`, native Anthropic messages body, image blocks |
| Everything else: compile, entailment, Red-Hat, surgical edit, field labels, compare | Claude Sonnet 5.5 (`anthropic.claude-sonnet-5-5`) | Bedrock, for cost |

- **Auth:** **only** the Bedrock API key (bearer token) in
  `AWS_BEARER_TOKEN_BEDROCK`. Do not require IAM for Bedrock.
- AWS access keys or the instance role are used **only for S3**.
- **Region:** `ASSURE_BEDROCK_REGION` (default us-east-1). Bare model ids get the
  inference-profile prefix for that region automatically (`us.` / `eu.`).
- **Other providers:** none. No OpenRouter, no direct Anthropic API, no
  DeepSeek, no Ollama.
- Model ids are configurable through env vars, but the defaults above are the
  product.

---

## 5. Architecture

```
Browser
  → web (Flask or FastAPI, gunicorn)     UI + /api; never parses, never runs Z3
  → PostgreSQL                           the only database
  → Redis                                Celery broker/results, locks, rate limits, spend counter
  → worker (Celery, queue "parse" + "verify")   rasterise, Opus, fields, Z3, Red-Hat, compile
  → object store                         S3 (ASSURE_S3_BUCKET) or <ASSURE_DATA_DIR>/objects
  → Amazon Bedrock                       Opus 5.5 / Sonnet 5.5, API-key auth
```

- **One Docker image** runs both web and worker, built by GitHub Actions for
  linux/arm64 and pushed to ghcr.
- **Deployment:** a single **EC2 Graviton host** with `docker compose up -d`. The
  services are postgres, redis, app, worker, and optionally backup.
- No Terraform and no ECS work in this rewrite. The user sets up infrastructure
  by hand.
- **No per-instance state.** Files go to the object store, counters and locks go
  to Redis, and everything else goes to PostgreSQL.
- **PostgreSQL only.** Use real PostgreSQL SQL, with migrations as numbered files
  run at startup (or alembic). No SQLite anywhere, including tests.
- **UI:** server-rendered pages plus vanilla JS modules, or a small SPA. Either is
  fine, but there is **one** UI. Requirements:
  - All strings come from i18n catalogs in 7 locales (en, es, zh, fr, de, ja,
    tr). A new key is added to all of them in the same change.
  - Static assets are cache-busted by a version string.

### 5.1 Data model (minimum)
- `projects`
- `sources`: id, project, filename, mime, pages, original_key, current_revision
- `source_revisions`: source, n, analysis_key, model, prompt_version, read_at
- `ingest_jobs`: id, source, stage, error, timings, model, pages_sent,
  pages_truncated
- `fields`: id, source, revision, page, key, label, value, kind, bbox, key_bbox,
  handwritten, illegible, status, schema_field nullable
- `field_events`: audit log of accept, correct, dispute and resolve
- `tables`: source, revision, page, headers, rows, bbox
- `page_texts`: source, revision, page, text
- `drafts`, `draft_revisions` (tree JSON), `claims` (draft_revision, node id,
  text, citations), `claim_verdicts` (anchoring, entailment, z3, redhat)
- `model_calls`: ledger of model, tokens, cost, latency, project, job
- `users`, `sessions` (when accounts are on)

Use `INSERT … ON CONFLICT` for anything a retried task could insert twice.

### 5.2 API (shape)
- `POST /api/projects/<p>/sources` (multipart) → 202 `{task_id, source_id, job_id}`
- `GET  /api/tasks/<id>` → `{state, stage, error}`
- `GET  /api/projects/<p>/sources`, `GET …/sources/<s>`
- `GET  …/sources/<s>/original`, `…/pages/<n>.jpg`, `…/analysis`
- `GET  …/sources/<s>/fields`
- `POST …/fields/<f>/accept|correct|dispute`, `POST …/disputes/<d>/resolve`
- `GET  …/fields/<f>/history`
- `POST …/sources/<s>/type` (document type override)
- `GET  /api/projects/<p>/fields/export?format=csv|json&wide=1`
- `POST /api/projects/<p>/compile` → SSE stream; `GET …/drafts/<d>`, `…/revisions`
- `POST …/drafts/<d>/nodes/<n>/edit` → 202 → re-verified node
- `POST …/drafts/<d>/verify`, `GET …/drafts/<d>/verdicts`
- `GET  …/drafts/<d>/export?format=pdf|json`
- `GET  /health`

Error shape: `{"ok": false, "error": "<human message>"}` with the correct status.
Long work returns 202 with a `task_id`.

---

## 6. Non-negotiables

1. **Opus 5.5 reads every upload, as page images.** There is no other parser or
   fallback, and nothing is labelled "Textract", "JDF", or "OCR" in the UI.
2. **The analysis is stored as received (after validation) and drawn as
   received.** What the user sees is what Opus said.
3. **Never fabricate.**
   - No invented confidence, no default "PASS", no "verified" without an earned
     check.
   - Z3 timeout means `TIMEOUT`. A missing source means `no_source`. A model
     error means `ERROR`.
4. **Nothing slow on the web tier.** Rasterising, model calls and Z3 run on the
   worker.
5. **PostgreSQL only, Redis for coordination, an object store for files.**
6. **Bedrock is authenticated only by API key.**
7. **One code path per job.**
   - One upload endpoint and one ingest function.
   - One place that builds fields from the analysis.
   - One renderer for overlays.
   - If you are about to add a second path for the same thing, stop and merge.
8. **Errors are shown, not swallowed.** A failed stage writes its error on the job
   and the UI shows it.
9. **Secrets are never committed.** `.env.example` lists every key, with secrets
   left empty.

---

## 7. Configuration (`.env`)

```env
# models
AWS_BEARER_TOKEN_BEDROCK=
ASSURE_BEDROCK_REGION=us-east-1
ASSURE_BEDROCK_MODEL_PARSE=anthropic.claude-opus-5-5
ASSURE_BEDROCK_MODEL_DEFAULT=anthropic.claude-sonnet-5-5
ASSURE_BEDROCK_MODEL_COMPARE=anthropic.claude-sonnet-5-5
ASSURE_PARSE_PAGES_PER_REQUEST=8
ASSURE_PARSE_CONCURRENCY=4
ASSURE_PARSE_TIMEOUT_S=600
ASSURE_BEDROCK_MONTHLY_USD_CAP=200

# storage
ASSURE_S3_BUCKET=
ASSURE_S3_PREFIX=assure/
AWS_DEFAULT_REGION=us-east-1
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
ASSURE_DATA_DIR=./data

# infra
DATABASE_URL=postgresql://assure:assure@postgres:5432/assure
REDIS_URL=redis://redis:6379/0
POSTGRES_PASSWORD=

# app
SECRET_KEY=
ENCRYPTION_KEY=
SHELL_ACCESS_KEY=
ASSURE_AUTH_MODE=off
ENVIRONMENT=production
PROXY_FIX_HOPS=1
MAX_UPLOAD_SIZE_MB=25
ASSURE_MAX_PAGES=200
GUNICORN_WORKERS=2
GUNICORN_THREADS=8
WORKER_CONCURRENCY=2
```

`POSTGRES_PASSWORD`, `DATABASE_URL` and `REDIS_URL` belong to the compose stack's
own postgres/redis containers. The password must be the same in both
`POSTGRES_PASSWORD` and `DATABASE_URL`, or compose must build `DATABASE_URL` from
`POSTGRES_PASSWORD`. Ship a `scripts/gen-env.sh` that writes `.env` from
`.env.example` and generates:
- `POSTGRES_PASSWORD` (`openssl rand -hex 24`), and `DATABASE_URL` from it;
- `SECRET_KEY` (`openssl rand -hex 32`);
- `ENCRYPTION_KEY` (a Fernet key);
- `SHELL_ACCESS_KEY`.

The script asks only for `AWS_BEARER_TOKEN_BEDROCK` and the S3 bucket and keys.

The app refuses to start in production without `DATABASE_URL`, `REDIS_URL`,
`SECRET_KEY` and `AWS_BEARER_TOKEN_BEDROCK`, and it says which one is missing.

---

## 8. Quality bar and acceptance

The rewrite is done when all of the following hold on the EC2 host:

1. **Handwritten scan.** A one-page photo of a handwritten form shows:
   - the image with boxes on every field Opus returned, at the right places
     (within the model's estimate);
   - the fields panel listing them, with the handwriting flags;
   - illegible values as `[illegible]`.
2. **Digital PDF with tables.** A multi-page PDF shows:
   - the PDF itself;
   - a reading panel with every table as an HTML table, matching the PDF's rows
     and columns.
3. **Mixed upload.** Ten files uploaded at once all reach `done` or `failed`, with
   a reason for each failure, and none is stuck in a stage.
4. **Bedrock off.** With a wrong API key, the job fails with Bedrock's auth error
   visible in the UI and in `/health`. There is no silent fallback output.
5. **Review.** Accept, correct, dispute and resolve each change the field and
   write an audit row. A corrected value is the one the compiler uses.
6. **Compile.** A draft streams in, every claim has a clickable citation that
   opens the source page with the box highlighted, and each claim has an
   entailment verdict.
7. **Numbers.** A claim with an inconsistent total gets Z3 `FAIL` with the
   violated constraint, and a consistent one gets `PASS`.
8. **Surgical edit.** Editing one paragraph changes only that node, and the node
   is re-verified.
9. **Export.** The dossier PDF opens, and its contents match the UI.
10. **Restart.** After `docker compose restart postgres redis`, uploading still
    works with no pool or connection errors.
11. **Clean console.** There are no browser console errors on any screen.

---

## 9. Build order

1. Skeleton:
   - compose (postgres, redis, app, worker);
   - config loading with the start-up checks;
   - migrations;
   - `/health`.
2. Object store (S3 or local), upload endpoint, ingest_jobs, Celery task, and
   task polling.
3. Rasterising, the Bedrock Opus reader, the analysis schema and validation, and
   `analysis.json` storage.
4. Source view: the PDF as is, or page images with overlays, plus the reading
   panel.
5. Fields derived from the analysis, the review actions with the audit log, the
   document type, and export.
6. Compile with Sonnet over SSE, the draft tree, and citations.
7. Verification: anchoring, entailment, Z3 and Red-Hat, with the verdict UI.
8. Surgical edit, compare, and the dossier export.
9. Accounts, the spend cap, i18n completeness, and polish.

Ship each step working end to end before starting the next.

---

## 10. Out of scope for this rewrite

- jdf-cli / JDF format, tesseract, Textract, OCR engines of any kind.
- OpenRouter, Ollama, local models, other LLM providers.
- Terraform / ECS / Fargate.
- Semantic or embedding search. Keyword search over page texts and fields is
  enough.
- The marketing landing site, Stripe/billing, Supabase, Plausible, Sentry.

---

## 11. Code conventions

- Python 3.11, typed, with small modules. Business logic lives in `services/`, SQL
  in `db/`, and HTTP in `routes/`.
- A docstring states *why*, with evidence (date, measurement). There are no
  decorative comments.
- Env flags are parsed as `os.environ.get("X","").strip().lower() in
  ("1","true","yes")`.
- Commit prefixes are `feat(scope):`, `fix(scope):` and `docs(scope):`.
- `docs/anti-claims.md` lists what the product must not claim (for example "bbox
  is measured", "confidence is calibrated"). Keep it current.

---

## 12. Lessons from the previous implementation (do not repeat)

- **Too many parse paths.** The upload route, the Sources panel and a legacy
  `/jdf/ingest` each built bundles differently. Textract, jdf-cli and tesseract
  sat behind "auto" routing and fallbacks. The result: a deployment with
  `PARSER_BACKEND` missing from its env silently used tesseract instead of Opus.
  **One path, one parser, no fallback.**
- **The model's reading was squeezed into another format** (JDF, Textract-shaped
  blocks) before it was shown. Information was lost on the way, and the UI
  labelled Opus's fields as "Textract". **Store and draw the model's own
  structure.**
- **PDFs went to Bedrock as a document block**, and scans came back without
  fields. **Send page images.**
- **Scores computed with no measurement behind them** (page quality, default
  confidences) confused users and reviewers. **Show only what was measured or
  stated by the model.**
- **Connection pools were created before fork, and `get_db()` was called outside
  a unit of work**, which killed the worker. **Open the DB per task, inside a
  scope, after fork.**
