# Research synthesis workflow — PR 1 & PR 2 (revised)

**Date:** 2026-09-09
**Status:** Spec approved — **implementation blocked on staging EC2** until [staging-launch-execution.md](./staging-launch-execution.md) Phase 1 completes. All new state **must persist to EC2 SQLite**, not IndexedDB/LocalStorage.

**Why backend is required:** Active draft already saves via `PUT /api/drafts` (`founder_draft.js` → `drafts` table). Run history lives in `runs`. Iteration lineage, draft snapshots, and verify/Red-Hat on the **merged** draft cannot be browser-only without breaking cross-device continuity.

---

## What already exists (do not rebuild)

| Capability | Location | Notes |
|------------|----------|-------|
| Active draft persistence | `drafts` table, `PUT/GET /api/drafts` | One row per `workspace_id` (= `projects.id`) |
| Run list + detail API | `GET /api/runs`, `GET /api/runs/<id>` | Detail includes Red-Hat findings when using `get_run_with_findings` |
| Per-run Red-Hat | `POST /api/runs/<id>/redhat` | Not the same as Red-Hat on merged Active Draft |
| Run creation + SSE | `POST /api/runs`, `POST /api/runs/execute` | `RunCreatePayload` — extend, don't duplicate |
| Z3 / lock verify helper | `auto_compiler._verify_locks`, `TruthLedgerEngine` | Reuse for draft verify |
| Cross-run compare (partial) | `GET /api/runs/<id>/contradictions?run_ids=` | PR 2 can build on this |
| Legacy JDF history | `jdf_revisions` | **Legacy compiler path** — not founder Active Draft snapshots |

**Schema today:** `_SCHEMA_VERSION = 22` in `prompt_matrix/db/connection.py` (`runs`, `drafts`, `redhat_findings`).

---

## Schema migration v23 (PR 1)

Add `_migrate_v23` and bump `_SCHEMA_VERSION` to **23**.

```sql
-- Iteration lineage
ALTER TABLE runs ADD COLUMN parent_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_runs_parent ON runs(parent_run_id);

-- Named draft snapshots (separate from active `drafts` row)
CREATE TABLE draft_snapshots (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    content TEXT NOT NULL,           -- JSON: founder JDF tree (same shape as drafts.content)
    user_notes TEXT NOT NULL DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (workspace_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_draft_snapshots_ws ON draft_snapshots(workspace_id, created_at DESC);
```

**Naming fixes vs original prompt:**

| Original | Revised |
|----------|---------|
| `FOREIGN KEY (workspace_id) REFERENCES workspaces(id)` | **`projects(id)`** — matches v21 `runs` / `drafts` FKs |
| `content JSON` column type | **`TEXT`** storing JSON — consistent with existing tables |
| Extend `jdf_revisions` for founder history | **New `draft_snapshots`** — avoids mixing legacy compiler revisions with founder draft |

Optional PR 1 follow-up (not blocking): `draft_redhat_findings` table keyed by `workspace_id` + snapshot id if findings must survive refresh without re-run.

---

## PR 1 — Backend API (new / extended)

### 1.1 Draft snapshots — `prompt_matrix/routers/drafts_routes.py`

| Method | Path | Body | Response |
|--------|------|------|----------|
| POST | `/api/drafts/snapshot` | `{ workspace_id, content, user_notes? }` | `{ ok, snapshot }` |
| GET | `/api/drafts/snapshots?workspace_id=` | — | `{ ok, snapshots[] }` |
| POST | `/api/drafts/restore` | `{ workspace_id, snapshot_id }` | `{ ok, draft }` — also `upsert_draft` |

Repository: `prompt_matrix/db/draft_snapshots_repository.py` (mirror `drafts_repository.py` patterns).

### 1.2 Iterate — extend `RunCreatePayload` + pipeline

**File:** `prompt_matrix/routers/runs_routes.py`, `services/auto_compiler.py`, `db/runs_repository.py`

```python
class RunCreatePayload(BaseModel):
    directive: str = ""
    workspace_id: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    model: str = "gemini"
    stream: bool = False
    parent_run_id: str | None = None      # NEW
    previous_context: str | None = None   # NEW — optional override; default from parent run body
```

- If `parent_run_id` set: validate run exists in same workspace; persist `parent_run_id` on insert.
- Inject into compiled prompt (reuse language guard):

  > The following is the output of a previous run. Use it as context for your response.\n\n{previous_context}

- Default `previous_context` from parent run: serialize `content` body text if client omits it.
- Response includes `parent_run_id` in run JSON (`_row_to_run`).

### 1.3 Draft verify — new service + route

**POST `/api/drafts/verify`**

```json
{ "workspace_id": "default", "content": { /* JDF tree */ }, "text": "optional plain-text override" }
```

**Implementation sketch:** `services/draft_verify.py`

1. Flatten JDF → plain text (reuse `founder_draft` / JDF helpers).
2. Extract claims (`claims_from_text` / `extract_claims_from_stream`).
3. Ground against workspace substrate (`detect_sources` / vault text).
4. Run `_verify_locks` + groundrails where sources exist.
5. Return `{ ok, locks, status: "passed"|"failed", findings: [] }`.

Do **not** fork Z3/DeepSeek prompt templates — call existing helpers.

### 1.4 Draft Red-Hat — new service + route

**POST `/api/drafts/redhat`**

Same payload shape as verify. Delegate to `founder_redhat` adversarial path adapted for arbitrary text (not only stored run row). Return `{ ok, findings, status: "completed"|"failed" }`.

Persist findings: either attach to a synthetic run row or return ephemeral findings for inline UI (PR 1 may return ephemeral; persist in PR 1.1 if needed).

---

## PR 1 — Frontend (Stack on founder shell)

| Feature | Files | Notes |
|---------|-------|-------|
| Run detail view | `runs_stack.js` or new `run_detail.js` | Fetch `GET /api/runs/<id>`; modal with directive, output, locks, findings, sources, toolbar |
| Iterate | Modal + `command_bar.js` / runs API | `POST /api/runs` with `parent_run_id`, `previous_context`; badge "Iteration of …" |
| Verify draft | `founder_draft.js` + header buttons | `POST /api/drafts/verify`; render lock pills via existing TipTap hooks |
| Red-Hat draft | Same | `POST /api/drafts/redhat`; reuse finding accept/dismiss from runs stack |
| Draft history | Header "History" modal | `GET /api/drafts/snapshots`, `POST /api/drafts/restore`; manual snapshot via `POST /api/drafts/snapshot` on significant edits |

**Persistence rule:** `founder_draft.js` keeps `PUT /api/drafts` autosave. Snapshots are explicit user actions or pre-verify checkpoints — never LocalStorage.

**i18n:** All new strings → `i18n.py` all 7 locales + `data-i18n` + `ui_cache` bump (`assure-128` target).

**Visual:** Modals use existing workbench tokens (`--spacing-*`, 44px touch targets, `btn-primary` / `btn-outline` hierarchy).

---

## PR 1 — Tests

| Test | File |
|------|------|
| `test_draft_snapshots_crud` | `tests/test_draft_snapshots.py` |
| `test_run_iterate_parent_id` | `tests/test_run_creation.py` |
| `test_draft_verify_endpoint` | `tests/test_draft_verify.py` |
| `test_draft_redhat_endpoint` | `tests/test_draft_redhat.py` |
| Playwright: run detail, iterate, verify, history | extend `tests/playwright/test_founder_workbench.py` |

---

## PR 1 — Git workflow (when ready)

```bash
git checkout staging
git pull origin staging
git checkout -b feat/research-synthesis-pr1
# … implement …
pytest tests/test_draft_snapshots.py tests/test_run_creation.py tests/test_draft_verify.py -q
# Playwright via CI or local embedded app
git commit -m "feat: research synthesis PR1 — run detail, iterate, draft verify/redhat, snapshots"
git push -u origin feat/research-synthesis-pr1
gh pr create --base staging --title "feat: research synthesis PR1" …
```

**Deploy:** After merge to `staging`, run EC2 Phase 1 redeploy — **not before disk cleanup**.

---

## PR 2 — Compare & merge (frontend-heavy)

**Branch:** `feat/research-synthesis-pr2` off `staging` (after PR 1 merged).

| Feature | Backend | Frontend |
|---------|---------|----------|
| Runs search & filter | Reuse `GET /api/runs?workspace_id=` | Client-side filter in `runs_stack.js` (+ optional `q` query param later) |
| Run compare | Reuse `GET /api/runs/<id>/contradictions?run_ids=` | Side-by-side diff UI, synced scroll |
| Selective merge | None | Checkbox per paragraph → `AssureFounderDraft.appendBlocks()` |

**No new tables in PR 2** unless search must scale beyond ~100 runs (then add `q` server filter only).

---

## Execution order (full program)

| Order | Work | Depends on |
|-------|------|------------|
| 1 | [staging-launch-execution.md](./staging-launch-execution.md) Phases 1–2 | EC2 available |
| 2 | Marketing visual QA + R2 deploy (`LANDING_CSS=59`) | Optional parallel |
| 3 | **PR 1** research synthesis backend + UI | Staging healthy for QA |
| 4 | **PR 2** compare / merge / search | PR 1 merged |
| 5 | Production promote `staging` → `main` | CI + manual gates |

---

## Cursor execution prompt — PR 1 (copy-ready)

```markdown
Read docs/runbooks/research-synthesis-pr1-pr2.md and OMP memories: assure-ai-project-spec, assure-ai-coding-standards, assure-ai-gap-analysis, assure-ai-task-status.

MISSION: Implement PR 1 on branch feat/research-synthesis-pr1 off staging.

BACKEND (required):
1. Migration v23: draft_snapshots + runs.parent_run_id (see runbook SQL).
2. draft_snapshots_repository + extend drafts_routes (snapshot/list/restore).
3. Extend RunCreatePayload + insert_run + auto_compiler for parent_run_id/previous_context.
4. POST /api/drafts/verify and POST /api/drafts/redhat — reuse _verify_locks and founder_redhat helpers.

FRONTEND:
1. Run detail modal (GET /api/runs/<id> already exists).
2. Iterate button + modal → POST /api/runs with parent fields.
3. Active Draft header: Verify, Red-Hat, History (snapshots API).
4. i18n all locales; bump APP_JS in ui_cache.py.

TESTS: pytest for new routes/repos; extend Playwright founder suite.

CONSTRAINTS:
- Persist to SQLite only (drafts + draft_snapshots + runs).
- Do not change core Z3/DeepSeek prompt templates.
- Match founder shell design system (48px rail, btn hierarchy).
- Do not push to staging/main until user confirms GitHub/EC2 ready.

OUTPUT: Code + test results + PR URL.
```

---

## Cursor execution prompt — PR 2 (copy-ready)

```markdown
Read docs/runbooks/research-synthesis-pr1-pr2.md. PR 1 must be merged first.

MISSION: feat/research-synthesis-pr2 off staging — compare, selective merge, runs search/filter.

FRONTEND ONLY (unless GET /api/runs?q= needed at scale):
1. Search bar + filters on #runs-stack (client-side on loaded runs).
2. Multi-select runs → Compare view (side-by-side diff, synced scroll).
3. Selective merge → AssureFounderDraft insert selected paragraphs.

Reuse GET /api/runs/<id>/contradictions for cross-run diff hints where helpful.

TESTS: Playwright test_runs_search, test_compare_view, test_selective_merge.

CONSTRAINTS: No LocalStorage for run content; match PR 1 modal patterns.
```
