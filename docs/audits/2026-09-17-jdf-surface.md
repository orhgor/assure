# JDF Surface Audit — 2026-09-17

Read-only, 5 isolated scouts, 53s, $0.07.

## A — Routes and auth posture

### Routes

| # | Method | Path pattern | Decorator | Scope |
|---|---|---|---|---|
| 1 | GET | `/api/projects/<project_id>/history` (jdf_routes.py:140) | `@project_ownership_required` (:141) | project-scoped |
| 2 | POST | `/api/projects/<project_id>/restore` (:162) | `@project_ownership_required` (:163) | project-scoped |
| 3 | GET | `/api/projects/<project_id>/jdf` (:179) | `@project_ownership_required` (:180) | project-scoped |
| 4 | PUT | `/api/projects/<project_id>/jdf` (:198) | `@project_ownership_required` (:199) | project-scoped |
| 5 | POST | `/api/projects/<project_id>/import-pdf` (:284) | `@project_ownership_required` (:285) | project-scoped |
| 6 | GET | `/api/projects/<project_id>/nodes/<node_id>/history` (:336) | `@project_ownership_required` (:337) | project-scoped |
| 7 | POST | `/api/projects/<project_id>/nodes/<node_id>/restore` (:~349) | `@project_ownership_required` (:~350) | project-scoped |
| 8 | POST | `/api/projects/<project_id>/jdf/ingest` (jdf_memory_routes.py:30) | `@project_ownership_required` (:31) | project-scoped |
| 9 | POST | `/api/projects/<project_id>/jdf/search` (:54) | `@project_ownership_required` (:55) | project-scoped |
| 10 | GET | `/api/projects/<project_id>/jdf/health` (:68) | `@project_ownership_required` (:69) | project-scoped |

No route in either file lacks a decorator; there are no global (non-`<project_id>`) routes in these two files.

### What the decorator actually enforces

`project_ownership_required` (middleware.py:74-88) reads `project_id` from kwargs else regex `^/api/projects/([^/]+)` (middleware.py:11), then calls `check_project_ownership` (middleware.py:44-70):

1. `if not project_id or not ownership_enforced(): return None` → **allow** (middleware.py:46-47).
2. `user_id = current_user_id() or session.get("clerk_user_id")`; empty → **401** `{"ok":false,"error":"Authentication required."}` (middleware.py:49-53).
3. `owner = project_owner_id(project_id)`; `if owner is None: return None` → **allow** (middleware.py:59-61).
4. `str(owner) != str(user_id)` → **403** `Forbidden.` (middleware.py:62-64).

**Gate:** `ownership_enforced()` (middleware.py:23-37). `ASSURE_ENFORCE_OWNERSHIP` (middleware.py:24) with `1/true/yes/on` → True, `0/false/no/off` → False; **unset** falls through to `bool(auth_required() and current_user_id())` (middleware.py:35). `auth_required()` (cloud_auth.py:109-112) is **False** when `ASSURE_EDITION`/`PEM_EDITION` is `self-hosted` (cloud_auth.py:74-77), else `clerk_configured()` (needs both `CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY`, cloud_auth.py:66-67). `current_user_id()` reads session key `clerk_user_id` (cloud_auth.py:131-133).

**Verdict: flag- and deployment-gated, no-op by default in self-hosted or keyless deployments.** With `ASSURE_ENFORCE_OWNERSHIP` unset and no Clerk session (or `ASSURE_EDITION=self-hosted`), `ownership_enforced()` is False and every route above returns 200 to an unauthenticated caller. Enforcement (401/403) only bites when `ASSURE_ENFORCE_OWNERSHIP` is truthy, or when Clerk is configured, non-self-hosted, and the request carries a session user. A secondary global guard `register_security_guards` (middleware.py:93-105, registered at web.py:374) re-runs the identical check for all `/api/projects/<id>/*` paths, so it shares the same gate and is equally inert when the flag is off. None of the 10 JDF paths appear in `PUBLIC_API` (cloud_auth.py:24-43), but that only matters for the Clerk login layer, not the ownership check.

### AUTH GAPS

- **Routes with no auth decorator:** none — all 10 routes carry `@project_ownership_required`.
- **Routes whose decorator does not actually enforce ownership:** **all 10** are effectively unenforced whenever `ownership_enforced()` is False — i.e. `ASSURE_ENFORCE_OWNERSHIP` unset/false and (`auth_required()` False [self-hosted edition or missing Clerk keys] or no session user). Under that condition each returns data/writes without a 401/403:
  - jdf_routes.py: `GET .../history`, `POST .../restore`, `GET .../jdf`, `PUT .../jdf`, `POST .../import-pdf`, `GET .../nodes/<node_id>/history`, `POST .../nodes/<node_id>/restore`.
  - jdf_memory_routes.py: `POST .../jdf/ingest`, `POST .../jdf/search`, `GET .../jdf/health`.
- **Even when the gate is ON, unowned projects are open:** `check_project_ownership` returns None (allow) when `project_owner_id(project_id) is None` (middleware.py:60-61), so any authenticated session can read/write any project row lacking an owner — applies to all 10 routes.

Evidence read: prompt_matrix/routers/jdf_routes.py (full), prompt_matrix/routers/jdf_memory_routes.py (full), prompt_matrix/middleware.py (full), prompt_matrix/cloud_auth.py:1-135, prompt_matrix/web.py:370-376,1595-1605.

## B — Storage schema

**1. DDL (verbatim)** — `prompt_matrix/services/jdf_memory.py:31-38`:

```sql
CREATE TABLE IF NOT EXISTS jdf_cli_documents (
    doc_id TEXT PRIMARY KEY,
    doc_hash TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    jdf_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT (datetime('now'))
);
```

Created lazily by `_ensure_jdf_cli_documents_table()` (`jdf_memory.py:42-46`), **not** in `init_db()`'s migration chain (`db/connection.py:753-930` has no `jdf_cli_documents`); so it exists only after a `remember_jdf_document` call. Distinct from `jdf_documents` (`db/connection.py:837-844`, PyMuPDF revisions) — docstring `jdf_memory.py:22-23,49-52`.

**2. Duplicate `doc_id`** — `jdf_memory.py:55-65`:

```sql
INSERT INTO jdf_cli_documents (doc_id, doc_hash, tenant_id, jdf_json)
VALUES (?, ?, ?, ?)
ON CONFLICT(doc_id) DO UPDATE SET
    doc_hash = excluded.doc_hash,
    tenant_id = excluded.tenant_id,
    jdf_json = excluded.jdf_json,
    created_at = datetime('now')
```

- Plainly: **upsert**, not an error, not a no-op.
- Overwritten: `doc_hash`, `tenant_id`, `jdf_json`, **and `created_at` (reset to now — the original is lost)**.
- Preserved: only `doc_id` (the conflict key); no other columns exist. `doc_id` is the sole PK — a second tenant reusing the same `doc_id` overwrites the first tenant's row.

**3. Where full JDF vs chunks live**

| Item | Store | Evidence |
|---|---|---|
| Full JDF (`{$jdf,meta,pages}` dict) | SQLite table `jdf_cli_documents.jdf_json`, `json.dumps(...)` — written **before** OMP | `jdf_memory.py:100-101,55-65` |
| Chunks (queryable index) | External OMP (Open Memory Protocol); `POST /v1/memories` | `omp_client.py:113-131`, `jdf_memory.py:107-125` |
| Chunk key | `f"jdf:{tenant_id}:{doc_id}:{idx}"` (OMP keeps key as **tag**, rows identified by OMP `id`) | `jdf_memory.py:122`; `omp_client.py:113` |
| Payload per chunk | dict → JSON string via `safe_omp_remember`: `{kind:"jdf_chunk", tenant, doc_id, doc_hash, chunk_idx, text[:8000], meta:{scalar keys of chunk minus text/content}}` | `jdf_memory.py:107-121`; `omp_client.py:169` |

- `idx` is the enumerate index, so keys **skip** for empty chunks (`continue` at `jdf_memory.py:104`).
- **Not stored durably if OMP writes fail: the chunk index — nothing.** No SQLite chunk table exists; only the full JDF row survives, yielding an orphaned doc row that is unsearchable.
- Failure mode: `safe_omp_remember` is best-effort and **never raises** — returns `None` on downtime/HTTP≥400 (`omp_client.py:160-178`). `remember_jdf_document` raises `OmpUnavailable(RuntimeError)` **only if `len(chunks)>0 and stored==0`** (`jdf_memory.py:127-128`); router maps that to HTTP 503 (`jdf_memory_routes.py:48-49`). **Partial writes are silent** — `0 < stored < len(chunks)` returns `200` with `chunks_stored` short, no error. The SQLite row is committed before OMP is touched, so OMP failure leaves that row behind (no rollback).
- Re-ingest does **not** overwrite/replace OMP chunks (new OMP memory rows); `get_doc_chunks` filters on `doc_id` only, not `doc_hash`, so stale chunks from a prior ingest can be returned alongside new ones (`jdf_memory.py:151-160`).

**4. Tenant scoping**

| Layer | Tenant use | Evidence |
|---|---|---|
| SQLite column | `tenant_id TEXT NOT NULL`; **not** part of the PK → no per-tenant uniqueness | `jdf_memory.py:35,32` |
| OMP key | `jdf:{tenant_id}:{doc_id}:{idx}` — namespacing prefix only | `jdf_memory.py:122` |
| OMP payload / read filter | `"tenant": tenant_id`, checked on read | `jdf_memory.py:108,145` |
| Value origin | `tenant_id=project_id` from URL path | `jdf_memory_routes.py:46` |
| Write enforcement | `@project_ownership_required` on ingest, but `check_project_ownership` returns `None` (allow) when `ownership_enforced()` is false | `jdf_memory_routes.py:30`; `middleware.py:60-61` |

- `ownership_enforced()` defaults to false on staging (`ASSURE_ENFORCE_OWNERSHIP` unset → true only if `auth_required() and current_user_id()`), matching `docs/anti-claims.md:12`. So tenant scoping is **path-derived and not verified against caller on write** — a caller may write any `project_id`'s tenant namespace.
- Read filters do check `parsed.get("tenant") == tenant_id` (`jdf_memory.py:145`), so cross-tenant reads are blocked at the payload level, but SQLite `_persist_jdf_document` has no read path exposed by these routes (no `SELECT` of `jdf_cli_documents` anywhere; only tests assert the INSERT).

## C — Client surface

**Every shell.js call reaching `/api/*/jdf*`** (single IIFE, `prototype/shell.js:1`–`:3462`). No `PUT /jdf` and no `/jdf/health` call exist in this file.

| Line | Method | URL pattern | URL built by | Project id used |
|---|---|---|---|---|
| 445 | POST (FormData `file`) | `/api/projects/<pid>/jdf/ingest` | `jdfProjectBase()` (`:428`) | `_sourceProjectId()`; empty ⇒ `"default"` |
| 472 | POST (JSON `{query}`) | `/api/projects/<pid>/jdf/search` | `jdfProjectBase()` (`:428`) | `_sourceProjectId()`; empty ⇒ `"default"` |
| 923 | GET `?version=<n>` | `/api/projects/<pid>/jdf` | inline concat (`_jumpToVersion`, `:919`) | `_activeProjectId()` (`:920`); early return if falsy (`:921`) |
| 1992 | GET | `/api/projects/<id>/jdf` | inline concat (`_switchProject`, `:1979`) | `_switchProject(id)` arg; no fallback (`:1981` returns if `!id`) |

Helper: `jdfProjectBase()` (`:428-432`) = `"/api/projects/" + encodeURIComponent(pid || "default") + "/jdf"` — the only helper building `/jdf/*` subpath URLs.

**Project-id resolution paths**

| Path | Definition | Return / fallback |
|---|---|---|
| `_sourceProjectId()` | `:278-282` | `SHELL.project.id || localStorage[STORAGE_KEY] || ""`; catch ⇒ `SHELL.project.id || ""`. Empty ⇒ coerced to `"default"` at `:431`. |
| `_activeProjectId()` #1 | `:814-818` | `SHELL.project.id || localStorage[STORAGE_KEY] || null` |
| `_activeProjectId()` #2 | `:1888-1890` | `SHELL.project.id || localStorage[STORAGE_KEY] || ""` — duplicate declaration in the same IIFE scope; hoisting makes this the effective definition for all callers. |
| `ensureProjectId()` | `:1765-1788` | existing id, else `POST /api/projects {title:"shell-proto"}`; on success persists id to `localStorage[STORAGE_KEY]` and `setShell("project.id", id)`. Returns a Promise. |
| `_switchProject(id)` | `:1979-1990` | explicit id; persists to `localStorage[STORAGE_KEY]`; `setShell("project.id", id)`. |

localStorage key: `STORAGE_KEY = "assure_project"` (`:13`), written on `project.id` via `_syncShellPathToDom` (`:126-127`). Fallback when nothing set: **`"default"`** (`jdfProjectBase`, `:431`).

**Tenant-isolation analysis**

Wrong/absent id ⇒ URL segment `default` ⇒ server binds `project_id="default"`.

- Guard: `@project_ownership_required` (`prompt_matrix/middleware.py:76`) → `check_project_ownership` (`:60-74`).
  - `ownership_enforced()` (`:24-34`) is `False` when `ASSURE_ENFORCE_OWNERSHIP` is unset/off **and** `auth_required()` false ⇒ returns `None` at `:62` ⇒ request allowed, no tenant check.
  - Even when enforced: `project_owner_id(project_id)` (`prompt_matrix/db/jdf_repository.py:35-40`) returns `None` for a nonexistent row, and `check_project_ownership` returns `None` on `owner is None` ⇒ allowed. `"default"` is never created by `POST /api/projects`, so it has no owner row.
  - Only a real owner mismatch yields 403 (`middleware.py:72-73`).
- Parameter role: `project_id` is a **namespace/filter key**, never merged with the authenticated user.
  - JDF routes: `get_project_jdf` (`jdf_routes.py:179-195`) → `fetch_latest_jdf_or_empty` / `fetch_jdf_at_version` → `SELECT ... WHERE project_id = ?` (`jdf_repository.py:136-140`, `:93-96`). Any caller passing the guard can read/write that project's revisions.
  - Memory routes: `tenant_id=project_id` → `remember_jdf_document` / `search_jdf_chunks` (`jdf_memory_routes.py:44`, `:59`); filter is `parsed.get("tenant") != tenant_id` and durable row key is `tenant_id` in `jdf_cli_documents` (`DEFAULT_TENANT = "default"`, `jdf_memory.py:19`). Same filter-only semantics.
- Net: with enforcement off, or for the unowned `"default"` namespace, an absent/wrong id lets the shell read and index against the shared `"default"` tenant. Isolation rests on `project_owner_id` matching a real owned row, which `"default"` lacks. `[INFERENCE]` Cross-tenant read of another *owned* project requires knowing/guessing its id and is 403-gated only when `ownership_enforced()` is true.

**Hardcoded / default tenant strings in shell.js**

| Line | String | Role |
|---|---|---|
| `:431` | `"default"` | URL fallback tenant/project segment in `jdfProjectBase()` — the only tenant literal |
| `:13` | `"assure_project"` | localStorage key (`STORAGE_KEY`), not a tenant value |
| `:1773` | `"shell-proto"` | project *title* sent to `POST /api/projects`, not an id/tenant |

No other hardcoded tenant literal exists in `prototype/shell.js`.

## D — Test coverage gap

**Naming caveat:** `tests/test_jdf_routes.py` tests **jdf_memory_routes.py** (ingest/search), not `jdf_routes.py`. `tests/test_jdf_converter.py` covers `prompt_matrix/services/jdf_converter.py` (`pdf_to_jdf`, `jdf_to_chunks`) — no route.

### 1–2. Route inventory + coverage

| Method & path (router) | Unit coverage | E2E coverage |
|---|---|---|
| `GET /api/projects/<project_id>/history` (jdf_routes) | `test_founder_restore.py::test_history_lists_revisions`; live smoke `quality_check/test_backend_endpoints.py:42` (asserts `200 or 401`) | `tests/playwright/test_left_pane.py:76` **mocks** the route (`page.route`→`fulfill`); never exercises backend |
| `POST /api/projects/<project_id>/restore` (jdf_routes) | `test_founder_restore.py::test_restore_updates_draft_and_returns_document`, `::test_restore_missing_version_returns_404` | `test_left_pane.py:93-129` mocks route; only asserts a `POST` fired |
| `GET /api/projects/<project_id>/jdf` (jdf_routes) | `test_import_config.py:61`, `test_inquire_stream.py:256`, `test_audit_log.py:31` (status only) | NONE (only `quality_check/test_backend_endpoints.py:66` `200 or 404`) |
| `PUT /api/projects/<project_id>/jdf` (jdf_routes) | `test_concurrency.py:77-85`, `test_document_lock.py:56-68`, `test_sanitization.py::test_xss_injection_blocked`, `test_founder_restore.py`, `test_inquire_stream.py:248` | Used as fixture setup in `tests/playwright/{conftest.py:34,test_export_pdf.py:25,test_version_slider.py:25}` — no assertions on response |
| `POST /api/projects/<project_id>/import-pdf` (jdf_routes) | `test_sanitization.py::test_xss_injection_blocked:92-100` (200 + payload sanitized) | NONE |
| `GET /api/projects/<project_id>/nodes/<node_id>/history` (jdf_routes) | `test_v12_features.py::test_node_revision_history_and_restore:170-174` | `e2e/test_rephrase_inline.py:137` waits for the request (no body assert); `e2e/prototype_surgical.spec.js:76` mocks it |
| `POST /api/projects/<project_id>/nodes/<node_id>/restore` (jdf_routes) | `test_v12_features.py::test_node_revision_history_and_restore:183-195` | NONE |
| `POST /api/projects/<project_id>/jdf/ingest` (jdf_memory_routes) | `test_jdf_routes.py::test_ingest_no_file`, `::test_ingest_wrong_ext`, `::test_ingest_oversized`, `::test_ingest_ok`, `::test_ingest_omp_unavailable`, `::test_auth_required_unauthenticated` | `e2e/prototype_jdf.spec.js:44-53` (asserts "Indexed N chunks"); `e2e/prototype_surgical_from_search.spec.js:23-27` |
| `POST /api/projects/<project_id>/jdf/search` (jdf_memory_routes) | `test_jdf_routes.py::test_search_empty_query`, `::test_search_ok` | `prototype_jdf.spec.js:58-69` (result row visible, matches `/liability|policy-sample/`, no "No matches"); `prototype_surgical_from_search.spec.js:30-35` |
| `GET /api/projects/<project_id>/jdf/health` (jdf_memory_routes) | **NONE** | **NONE** |

### 3. Routes with NEITHER unit nor E2E coverage

- `GET /api/projects/<project_id>/jdf/health` (`prompt_matrix/routers/jdf_memory_routes.py:69-72`)

All other 9 routes have ≥1 real (non-mocked) unit test.

### 4. Meaningfulness of each covered assertion

| Route | Verdict |
|---|---|
| `GET .../history` | **Meaningful** — asserts `count==2`, ordering (`history[0].version==2`), `mutation_type`, `change_summary`, timestamp presence (`test_founder_restore.py:88-98`). |
| `POST .../restore` | **Meaningful** — asserts restored body content *and* draft side-effect; 404 case is a real error path. |
| `GET .../jdf` | **Mixed** — `test_inquire_stream.py:256` asserts `document_id` round-trip (meaningful); `test_audit_log.py:31` and `test_import_config.py:61` are weak (status/existence). |
| `PUT .../jdf` | **Meaningful** — 409 on stale `expected_version` (concurrency), 409 on locked doc, XSS stripped from stored content. |
| `POST .../import-pdf` | **Meaningful** — asserts sanitized payload contains no `<script>`/`onerror`; no test of the 400 "No file"/"Empty file" branches. |
| `GET .../nodes/<id>/history` | **Meaningful-ish** — asserts `revisions` non-empty (≥1) and that the returned id is usable; does not assert version ordering. |
| `POST .../nodes/<id>/restore` | **Meaningful** — asserts restored node content re-appears in document body; no 404 (`revision not found`) or 400 (`revision_id required`) branch covered. |
| `POST .../jdf/ingest` | **Meaningful** — 413 size cap message, 400 wrong extension, 503 OmpUnavailable, real stored-chunk count; 500 `JdfConversionError` branch untested. |
| `POST .../jdf/search` | **Meaningful** — asserts `count` and `results[0].doc_id`; the `limit` int-coercion fallback (non-int `limit`) untested. |
| `GET .../jdf/health` | **Vacuous-or-absent** — no assertion at all; 200/503 branch unverified. |

**Converter tests** (`test_jdf_converter.py`): meaningful — success parse of written JSON/JSONL file and `JdfConversionError` on nonzero exit; no test of missing output file or bad JSON.

## E — Anti-claims check

`docs/anti-claims.md` exists (10-row table). No shell tool is mounted in this session — `git log/show` could NOT be run; evidence below is file:line from the working tree.

| Claim (short quote) | Verdict | Evidence |
|---|---|---|
| "OMP provides semantic search" | STILL ACCURATE | `omp_client.py:142` `body={..., "mode": "keyword"}`; docstring `:135` "keyword recall" |
| "Path A surgical edit passes" | STILL ACCURATE (static) | Runtime/auth claim; no code path changed it. `queue.md:8` (item 1, "Fix free-model / DeepSeek auth") still open |
| "Free-model stack is stable" | STILL ACCURATE (static) | `queue.md:8` item 1 still lists DeepSeek credential as blocker; `state.md` no resolved-auth entry |
| "Deploy reports success = app serves" | STILL ACCURATE | `state.md:96` "7. Deploy reports success without verifying app serves" |
| "Two JDF formats are compatible" | STILL ACCURATE | `jdf_memory.py:29-52` separates `jdf_cli_documents`; `:50-52` says output "does not match the app JDF schema that parse_document() requires"; `test_jdf_routes.py:120-144` pins dedicated table; `state.md:91` debt #1 |
| "Regulatory erasure is implemented" | STILL ACCURATE | `web.py:900` only `/api/account/delete`; grep for `forget_document`/`erase_document`/doc-level delete in `prompt_matrix/` = no matches; `state.md:93` debt #3 |
| "Auth is enforced on staging" | STILL ACCURATE | `state.md:48` `ASSURE_ENFORCE_OWNERSHIP` = off; `state.md:104-107` I1; `middleware.py:27-31` reads the env var |
| "Verification is surfaced end-to-end for the client" | STILL ACCURATE | grep `full_context_scan|macro_verify` in `prototype/shell.js` = no matches; refs only backend (`runs_routes.py:19`; `state.md:74-75`) |
| "The knowledge vault works from the prototype" | STILL ACCURATE | grep `vault_tfidf_cache|fast_router` = services/routers/tests only, never `prototype/` (`state.md:69,72`) |
| "All E2E suites are green" (reason: `prototype_surgical_from_search` FAILS — SEARCH→EDIT not implemented) | **STALE** | Bridge now exists: `prototype/shell.js:1155` `_openJdfSearchResultInEditor`; result row is a `div` with `role="button"` and click handler `:498-512` calling it; builds `.jdf-node[data-node-id]` paragraph (`:1156-1210`), sets selection and calls `_attachNodeRephrase` (`:1214-1220`); spec body now waits for the node (`prototype_surgical_from_search.spec.js:36-50`) |

### Stale / wrong claims and minimal corrections

- **"All E2E suites are green" — path-B clause only.** The reason "`prototype_surgical_from_search` FAILS (SEARCH→EDIT not implemented)" is stale. Correction: replace with "`prototype_jdf` passes; `prototype_surgical_from_search` now reaches the editor (search→edit bridge landed in `prototype/shell.js:1155`), so Path B is implemented; `golden_path` still FAILS (targets `/app` — `golden_path.spec.js:68`)." The first clause ("path B fails") in the *Correct phrasing* column is likewise stale; keep only "...`golden_path` fails."
- **Spec header comment is stale, not the anti-claims row.** `tests/e2e/prototype_surgical_from_search.spec.js:5-6` still says "EXPECTED OUTCOME: this is expected to FAIL — search results are plain divs with no node-id". Correction: drop that comment (the body at `:36-50` now asserts the node loads).

### Notes / non-claims
- Claims 2 and 3 (Path A / free-model stability) are runtime-credential claims; they cannot be confirmed or denied from static code and were not altered by the search→edit bridge. They remain consistent with the still-open queue item 1 (`docs/queue.md:8`).
- Could not execute `git --no-pager log/show` (b7183df, 0bb52fc, 897cf69): no shell tool is mounted for this subagent. Findings rest on the current working-tree contents only.

---
## Method notes
- 5 scouts, isolated CoW worktrees, read-only, parallel
- Cross-verified middleware.py:25-90 by parent
- Section E scout had no shell tool (disclosed inline)
