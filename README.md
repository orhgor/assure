# Assure - Document Verification Platform

**What it does:** You give it a source and a question. It writes a document, then verifies every claim against the source. Each claim carries a verification state (anchored, supported, partial, unanchored). A separate adversarial review (Red-Hat) checks if claims are defensible.

**Three checks:**
- Anchoring: is there a source for this claim?
- Entailment: does the source support this claim?
- Numeral audit: do the numbers reconcile?

**Staging:** `https://staging.getassureai.com`

---

## Architecture

```
Cloudflare Tunnel (staging.getassureai.com)
  → Shell gate (prototype/dev-server.py:8891)
    → Gunicorn (prompt_matrix.web:app:8765)
      → SQLite (prompt_matrix/history.sqlite)
      → Edge worker (assure-worker-staging.orhangorenn.workers.dev)
```

**Ports (clean working):**
- `8891` - Shell gate (entry point, staging infra) - **keep**
- `8765` - Gunicorn (backend, working) - **keep**

**Legacy (not clean working):**
- `8890` - Flask dev (returns 500) - **legacy/remove**
- `8892`, `8893`, `8894` - Unknown listeners - **confirm**

---

## Services (clean working)

- `assure-prototype-static.service` - Shell gate on 8891 - **keep (staging infra)**
- `assure.service` - Gunicorn on 8765 - **keep**

**Legacy:**
- `assure-prototype.service` - Flask dev on 8890 - **legacy/remove**

---

## UI/UX Flow (user journey)

### Step 1: Sign in
- User goes to `https://staging.getassureai.com`
- If `ASSURE_EDITION=self-hosted` (staging), sees "Self-hosted skips cloud login. Compose is open."
- Clicks "Continue to Compose" → lands on `/app` (workbench)

### Step 2: Upload source document
- In the workbench, user clicks "Upload document"
- Selects a PDF (or text file)
- System uploads to `/api/projects/<id>/substrate/upload`
- PDF is parsed by `pdf_to_parse_bundle()` (jdf_converter.py)
- Text-like files are handled by `_text_bundle_for_ingest()` (shell gate)
- Parser selection uses `select_parser()` (parser_router.py)
- Results: document metadata, chunks, confidence scores, OMP artifact
- User sees document in project list with parse status

### Step 3: Compile (grounding)
- User enters an intent (question/verification goal)
- Clicks "Compile"
- System fetches substrate entries via `fetch_substrate_entries_by_ids(substrate_file_ids)`
- Builds grounded context via `_build_substrate_context()` from parser-produced rows
- `has_substrate` gate ensures source is attached before audit pipeline runs
- LLM compiles document with claims, each claim stamped with:
  - `source_anchor` (paragraph↔source mapping, in `models/jdf.py`)
  - `confidence` (parse/OCR/structured scores from `confidence.py`)
- Returns streamed SSE with compiled document

### Step 4: Verify (entailment + z3)
- After compile, verification runs automatically
- `attach_entailment_to_tree()` runs semantic verification per anchored claim:
  - Is the source quoted?
  - Does the source support this claim?
- z3 compliance checks per claim
- Results stored in `meta.confidenceSpans` via `save_jdf_revision()`
- User sees verification states: anchored, supported, partial, unanchored

### Step 5: Red-Hat (adversarial review)
- User clicks "Red-Hat" on a document
- Runs adversarial review: "Does the source only imply this claim?" "Is this defensible?"
- Uses LLM to review each claim against the source
- Returns findings: claims that the source only implies, assertions without support
- User can click findings, see source quote, apply suggestions
- Stored in `meta.redhat` via `save_jdf_revision()`

### Step 6: Export
- User clicks "Export"
- System builds dossier with:
  - Compiled document (all claims with verification states)
  - Source manifest (each source with hash, name, size)
  - Version chain
  - Model that wrote it
- Downloads as PDF (OOXML), Markdown, HTML, or Word
- Includes readable memo

### Keep (proxied, working)
- `/` - Home
- `/signin` - Sign in (Clerk or self-hosted message)
- `/signup` - Sign up (Clerk or self-hosted message)
- `/parsing` - Parsing results dashboard
- `/api/*` - API endpoints (projects, ingest, auth, drafts, etc.)
- `/static/*` - Static files
- `/favicon.ico`, `/favicon.svg` - Favicon

### Confirm (exists in code, need shell gate update)
- `/connect` - Connect to provider (not proxied yet → 404)

### Remove (workbench/legacy, not essential for staging)
- `/workbench`, `/app`, `/compose`, `/history`, `/learn`, `/library`, `/architecture`

---

## Environment

**Staging (`.env.staging`):**
```
ASSURE_EDITION=self-hosted
ENVIRONMENT=staging
APP_HOST=staging.getassureai.com
SHELL_ACCESS_KEY=assure-staging-demo-key-2026
ASSURE_REQUIRE_LOGIN=false
ASSURE_MAX_PAGES=200
SUBSTRATE_INGEST_SECRET=...
CLOUDFLARE_TUNNEL_TOKEN=...
CLOUDFLARE_TUNNEL_ID=fe93535b-a2cb-461d-a8ef-143f07c35876
ASSURE_EDGE_WORKER_URL=https://assure-worker-staging.orhangorenn.workers.dev
```

**Key env vars (code):**
```
ASSURE_EDITION, PEM_EDITION     - "self-hosted" = no Clerk auth
CLERK_PUBLISHABLE_KEY, CLERK_SECRET_KEY - Clerk (not configured on staging)
DATABASE_PATH                   - SQLite path
SHELL_ACCESS_KEY                - Shell gate auth key
UPSTREAM_BASE                   - Backend URL (http://localhost:8765)
PORT, HOST                      - Shell gate port/host
```

**Other env files (committed):**
- `.env.local` - Local/dev env vars
- `.env.production` - Production env vars

---

## Files (keep)

**Core:**
- `prompt_matrix/web.py` - Flask app, routes
- `prompt_matrix/cloud_auth.py` - Clerk auth, `is_self_hosted()`, `clerk_configured()`
- `prompt_matrix/services/jdf_converter.py` - PDF parsing (`pdf_to_parse_bundle()`)
- `prompt_matrix/services/parser_router.py` - Parser selection (`select_parser()`)
- `prompt_matrix/db/substrate_repository.py` - Database (`list_substrate_for_project()`)
- `prompt_matrix/routers/jdf_memory_routes.py` - `/api/projects/<id>/jdf/ingest`, search
- `prompt_matrix/lib/logger.py` - Logging

**Templates (keep):**
- `prompt_matrix/templates/auth.html` - Auth pages (signin/signup)
- `prompt_matrix/templates/parsing.html` - Parsing results
- `prompt_matrix/templates/connect.html` - Connect to provider

**Prototype (staging infra - keep, not product surface):**
- `prototype/dev-server.py` - Entry gate server
- `prototype/index.html` - Shell UI
- `prototype/shell.js` - Shell JS
- `prototype/shell.css` - Shell styles
- `prototype/about.html` - About content
- `prototype/favicon.svg` - Favicon

**Config (keep):**
- `.env.staging` - Staging env vars

**Config (keep):**
- `.env.staging` - Staging env vars
- `.env.local` - Local/dev env vars
- `.env.production` - Production env vars

---

## CLI

```bash
# Run locally
cd prompt_matrix && python -m prompt_matrix.web --port 8890 --host 127.0.0.1

# Or with gunicorn
gunicorn --worker-class gevent --workers 4 --bind 0.0.0.0:8765 prompt_matrix.web:app
```

---
## Access Key & Parsing

### Staging (no access key required)
- `SHELL_ACCESS_KEY` is **not set** for staging
- The shell gate runs without an access key check
- `/parsing`, `/signin`, `/signup`, `/api/*` are all publicly accessible
- No Clerk required for staging

### Production (Clerk required)
- `SHELL_ACCESS_KEY` is set for production
- Clerk auth is configured (`CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`)
- `/signin`, `/signup` use Clerk
- `/api/*` require Clerk auth

### Parsing capabilities
- Parsing is **connected with the UI**, not hidden
- `/parsing` shows the parsing results dashboard (document list, confidence scores, verification status)
- The parsing functionality is accessible via `/api/projects/<id>/jdf/ingest` (upload) and `/parsing` (results)
- PDF parsing uses `pdf_to_parse_bundle()` in `jdf_converter.py`
- Text-like files are handled via `_text_bundle_for_ingest()` in the shell gate
- Parser selection uses `select_parser()` in `parser_router.py`

## Parsing → Assuring Connection

The parsing→assuring connection is **real and provable** in the live trace:

1. **Upload → Parse**
- User uploads PDF to `/api/projects/<id>/substrate/upload`
- `pdf_to_parse_bundle()` (jdf_converter.py) parses the PDF:
  - Extracts text, chunks, tables, figures, images
  - Computes parse confidence, OCR confidence
  - Builds JDF document tree
- `select_parser()` (parser_router.py) selects the right parser:
  - Born-digital PDF → JDF CI
  - Scanned PDF → Textract (if configured)
  - Text-like files → `_text_bundle_for_ingest()` (shell gate)
- Results stored in `substrate_vault` table (filename, page_count, parser_name, parse_confidence, ocr_confidence, asset_summary, OMP artifact)

2. **Parse → Compile (grounding)**
- User enters intent and clicks "Compile"
- `fetch_substrate_entries_by_ids(substrate_file_ids)` loads the **exact rows the parser produced** (text, page count, OMP artifact, confidence scores)
- `_build_substrate_context()` builds the grounded prompt from those rows:
  - Source text (from `extracted_text` column)
  - Source metadata (page count, confidence scores, source anchors)
- `has_substrate` gate ensures source is attached before audit pipeline runs
- LLM compiles document with claims, each claim stamped with:
  - `source_anchor` (paragraph↔source mapping, stored in `models/jdf.py`)
  - `confidence` (parse/OCR/structured scores from `confidence.py`)

3. **Compile → Verify (entailment + z3)**
- After compile, verification runs automatically
- `attach_entailment_to_tree()` runs semantic verification per anchored claim:
  - Is the source quoted? (anchoring check)
  - Does the source support this claim? (entailment check)
- z3 compliance checks per claim
- Results stored in `meta.confidenceSpans` via `save_jdf_revision()`
- User sees verification states in the UI: anchored, supported, partial, unanchored

4. **Verify → Red-Hat (adversarial review)**
- User clicks "Red-Hat" on a document
- Red-Hat reviews each claim against the source:
  - "Does the source only imply this claim?"
  - "Is this defensible?"
- Uses LLM to review each claim
- Returns findings: claims that the source only implies, assertions without support
- Stored in `meta.redhat` via `save_jdf_revision()`
- User can click findings, see source quote, apply suggestions

5. **Verify → Export (dossier)**
- User clicks "Export"
- System builds dossier with:
  - Compiled document (all claims with verification states from `meta.confidenceSpans`)
  - Source manifest (each source with hash, name, size)
  - Version chain
  - Red-Hat findings (from `meta.redhat`)
- Downloads as PDF (OOXML), Markdown, HTML, or Word
- Includes readable memo

### Key files in the parsing→assuring chain
- `prompt_matrix/services/jdf_converter.py` - `pdf_to_parse_bundle()` (PDF parsing)
- `prompt_matrix/services/parser_router.py` - `select_parser()` (parser selection)
- `prompt_matrix/routers/jdf_memory_routes.py` - `/api/projects/<id>/jdf/ingest` (upload), substrate operations
- `prompt_matrix/db/substrate_repository.py` - `list_substrate_for_project()` (load parser-produced rows)
- `prompt_matrix/routers/draft.py` - `/api/projects/<id>/draft/stream` (compile), `fetch_substrate_entries_by_ids()`, `_build_substrate_context()`, `attach_entailment_to_tree()`, `build_audit_summary()`
- `prompt_matrix/models/jdf.py` - JDF document model, `source_anchor`, `confidenceSpans`
- `prompt_matrix/services/confidence.py` - scoring (parse/OCR/structured/verification/redhat/document layers)
- `prompt_matrix/services/omp.py` - OMP artifact persistence (shared between parsing and assuring)
## Prototype Environment (Parsing vs Assuring)

### Prototype environment (shell gate)

The `prototype/` directory contains the **shell gate** — the entry point for staging:

- `prototype/dev-server.py` - Shell gate server on port 8891
- `prototype/index.html` - Shell UI (main page)
- `prototype/shell.js` - Shell JavaScript (application logic)
- `prototype/shell.css` - Shell styles
- `prototype/about.html` - About content (used in modal)
- `prototype/favicon.svg` - Favicon

**Role:** The shell gate is the **only thing the public hostnames reach**. It:
- Serves static files (shell UI, CSS, JS, about page, favicon)
- Proxies `/api/*` to gunicorn on 8765
- Proxies `PROXIED_PAGES` (`/signin`, `/signup`, `/signout`, `/parsing`, `/connect`) to gunicorn
- Handles auth (access key via `SHELL_ACCESS_KEY`, or Clerk if configured)
- Is the entry point for `staging.getassureai.com` (via Cloudflare Tunnel)

**Why it's separate from `prompt_matrix/`:** The shell gate is a standalone service that provides the entry point and static UI. The core Assure application (`prompt_matrix/`) runs on gunicorn (8765) and handles the business logic (parsing, compiling, verifying, Red-Hat, exporting).

### Parsing environment

Parsing uses the **full stack**:
- **Shell gate** (8891) - Entry point, auth, static serve, proxy
- **Gunicorn** (8765) - Flask app, parsing (`pdf_to_parse_bundle()`), substrate operations
- **Edge worker** (`assure-worker-staging.orhangorenn.workers.dev`) - Edge processing
- **SQLite** (`prompt_matrix/history.sqlite`) - Database, substrate vault

**Parsing flow:**
1. User uploads PDF to `/api/projects/<id>/substrate/upload` (via shell gate → gunicorn)
2. Gunicorn parses PDF with `pdf_to_parse_bundle()` (jdf_converter.py)
3. Parser selection with `select_parser()` (parser_router.py)
4. Results stored in `substrate_vault` table (SQLite)
5. OMP artifact created
6. User views results on `/parsing` (via shell gate → gunicorn)

### Assuring environment

Assuring uses **gunicorn + edge worker** (no shell gate needed for the backend):
- **Gunicorn** (8765) - Flask app, compile (`draft.py`), verify (`compile_guard.py`), Red-Hat (`orchestrator.py`), export
- **Edge worker** (`assure-worker-staging.orhangorenn.workers.dev`) - Edge processing (LLM calls, scoring, etc.)
- **SQLite** (`prompt_matrix/history.sqlite`) - Database, substrate vault, JDF revisions

**Assuring flow:**
1. User enters intent, clicks "Compile" (via shell gate → gunicorn)
2. Gunicorn fetches substrate entries, builds grounded context, calls LLM
3. LLM compiles document with claims, each stamped with source_anchor, confidence
4. Verification runs (`attach_entailment_to_tree()`, z3 checks)
5. User views verification states on `/parsing` (or in the workbench)
6. User clicks "Red-Hat" → gunicorn runs adversarial review (orchestrator.py)
7. Results stored in `meta.redhat` via `save_jdf_revision()`
8. User can view findings, apply suggestions, export

### How they're different

| Aspect | Parsing | Assuring |
|--------|---------|----------|
| **Entry point** | Shell gate (8891) for upload and viewing results | Shell gate (8891) for compile/verify/Red-Hat/export |
| **Backend** | Gunicorn (8765) - `pdf_to_parse_bundle()`, `select_parser()` | Gunicorn (8765) - `draft.py`, `compile_guard.py`, `orchestrator.py` |
| **Primary function** | Parse documents, extract text/chunks/tables/figures, compute confidence | Compile grounded documents, verify claims, adversarial review, export |
| **Key files** | `jdf_converter.py`, `parser_router.py`, `substrate_repository.py` | `draft.py`, `compile_guard.py`, `orchestrator.py`, `confidence.py` |
| **Output** | JDF document, chunks, confidence scores, OMP artifact | Compiled document with claims, verification states, Red-Hat findings, exportable dossier |
| **Env vars** | `ASSURE_MAX_PAGES`, `JDF_BIN`, `JDF_TIMEOUT`, `ASSURE_EDGE_WORKER_URL` | `OPENROUTER_API_KEY`, `ASSURE_EDGE_WORKER_URL` |

### How they're connected

- **Shared backend:** Both parsing and assuring run on gunicorn (8765) and use the same SQLite database
- **Shared OMP layer:** Both parsing and assuring use `services/omp.py` for artifact persistence and lineage tracking
- **Parsing feeds assuring:** Parsing produces substrate entries (text, chunks, confidence, OMP artifact) that compile uses as source context
- **Assuring depends on parsing:** Compile can't run without substrate entries (the `has_substrate` gate). Verification is grounded in the parsing results. Red-Hat reviews claims that parsing produced.
- **Same database:** `prompt_matrix/history.sqlite` stores both parsing results (substrate_vault) and assuring results (jdf_revisions, meta.confidenceSpans, meta.redhat, etc.)
- **Same edge worker:** Both use `assure-worker-staging.orhangorenn.workers.dev` for edge processing (LLM calls, scoring, etc.)

### Summary

Parsing and assuring are **two capabilities of the same platform**, sharing the same backend (gunicorn on 8765), same database (SQLite), same edge worker, and same OMP layer. The shell gate (prototype/) is the entry point for both, providing the UI and auth. Parsing produces the source material that assuring verifies. The two capabilities are deeply connected - you can't assure what you haven't parsed.

---

## Notes
## Notes
