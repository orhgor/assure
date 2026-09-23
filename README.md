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

## Routes (working public surface)

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

**Not for staging:**
- `.env.local` - local/dev only
- `.env.production` - production only

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

**Remove:**
- `prompt_matrix/templates/index.html` - Workbench template (if workbench removed)
- `.env.local` - Local/dev only
- `.env.production` - Production only

---

## CLI

```bash
# Run locally
cd prompt_matrix && python -m prompt_matrix.web --port 8890 --host 127.0.0.1

# Or with gunicorn
gunicorn --worker-class gevent --workers 4 --bind 0.0.0.0:8765 prompt_matrix.web:app
```

---

## Notes

- Staging uses `ASSURE_EDITION=self-hosted` - no Clerk auth required
- Production uses Clerk auth (`CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`)
- Shell gate requires `SHELL_ACCESS_KEY` for API access
- `.env.staging` is committed (was in `.gitignore`, now exceptioned)
- Workbench routes removed - only core Assure routes remain
- `prototype/` is staging infrastructure (entry gate + shell UI), not product surface
- `/connect` is in code but not proxied yet → 404 (confirm)
- Flask on 8890 is legacy (returns 500) - use gunicorn on 8765
