# Shell API Contract — Pipeline + Document

Scope: the five routes that drive the first shell screen
(stage viewer + document + Time Machine). Everything else
(report, compliance, sources, exports) is documented separately.

All routes require `project_ownership_required` unless noted.
All SSE responses use `Content-Type: text/event-stream`.

## Verified against runtime on 2026-09-13

All shapes below verified by firing real requests against local Flask.
Drift from original code-reading pass is documented in the relevant
sections.

---

## 1. POST /api/projects

Create a new empty project. Required before any project-scoped route.

### Request
```
{ "title": "<project name>" }
```

### Response 201
```
{
  "id": "<slugified-title>-<hash>",
  "ok": true,
  "prompt": "",                // always empty string for vanilla create
  "template_id": null,
  "title": "<echo of request title>"
}
```

---

## 2. POST /api/projects/<id>/draft/stream

Primary compile pipeline. Streams stages 1–6 of the orchestrator.

### Request
{
  "intent": "<user prompt>",           // required unless compileType=selection
  "context": "<optional context>",
  "substrate_file_ids": ["<id>", ...], // optional
  "compileType": "full" | "selection", // default: "full"
  "content": "<selected text>"         // required if compileType=selection
}

### Pre-stream errors (JSON, not SSE)
| Status | Body | When |
|---|---|---|
| 400 | {error: "content required for selection compile"} | selection without content |
| 400 | {error: "intent required"} | full without intent |
| 400 | {error: "<validation>"} | malformed payload |
| 429 | {error: "<daily limit>"} | DailyCompileLimitError |
| 429 | (Flask-Limiter default) | rate limit, 30/min |

### SSE events

Data payload convention: every `data:` JSON object has a top-level
`type` field whose value matches the `event:` name (e.g. `event: token`
→ `data: {"type":"token", "delta":"…"}`). Shell consumers can safely
key on either the `event:` line or `data.type`; both agree.

| Event | Data | When |
|---|---|---|
| status | `{type:"status", stage:"preflight", message:"Checking budget…", request_id}` | before model call |
| status | `{type:"status", stage:"model", message:"Drafting with Claude…", model}` | model starting |
| token | `{type:"token", delta:"<text>"}` | streaming tokens |
| usage | `{type:"usage", input_tokens, output_tokens, model_id, task_type}` | after EACH model call (fires N times per compile, once per LLM call: e.g. deep_synthesis + summarize_node = 2 usage events). Shell should render/update on every occurrence. |
| status | `{type:"status", stage:"locks", message:"Inferring locks…"}` | after draft text |
| status | `{type:"status", message:"Running Math Check…"}` | between compiled and verified — note: NO `stage` field. Shell MUST handle stage-less status events. |
| compiled | `{type:"compiled", document, nodes, locks, node_count, lock_count, draft_text}`. See detailed shape below. | JDF document and locks are assembled post-draft. |
| verified | `{type:"verified", ok, gate_status, z3_status, z3_results, redhat_critiques, redhat_count, redhat_results, document, confidenceSpans, confidence_spans, audit_manifest, claims}`. See detailed shape and field-duplication notes below. | Math (Z3) + red-hat post-processing complete. **This event is the authoritative terminal document state.** |
| error | `{type:"error", ok:false, error, http_status?, request_id}` | any failure. IMPORTANT: `error` MAY appear AFTER `verified` on a single stream (post-verification persistence failure). Shell MUST treat `verified` as authoritative and MUST NOT roll back the document if an error follows. |
| [DONE] | — | terminal sentinel. Emitted as a bare `data: [DONE]` line with NO `event:` wrapper (see "Terminal sentinel" below). |

### `compiled` event shape (detail)

```
{
  type: "compiled",
  document: { document_id, meta:{project_id, source}, truth_ledger:{canonical_key:value,…}, body:[...JDF nodes...] },
  nodes:     [...flat duplicate of document.body...]  // redundant; prefer document.body
  locks: [ { entity, metric, period, scenario, value, unit, confidence, canonical_key }, ... ],
  node_count: <int>,
  lock_count: <int>,
  draft_text: "<full markdown text of the draft>"
}
```

### `verified` event shape (detail)

```
{
  type: "verified",
  ok: true,
  gate_status: "pass" | "fail",
  z3_status:   "PASS" | "FAIL",
  z3_results: {
    status: "PASS" | "FAIL",
    violations: [ ... ],
    lock_results: [ { key, ok, value }, ... ],
    locks_verified: <int>,
    metrics_checked: <int>
  },
  redhat_critiques: [ ... ],
  redhat_count: <int>,
  redhat_results: [ ... ],
  document: { ...full JDF, matching `compiled.document` shape,
              plus meta.confidenceSpans:[...],
              plus body[*].meta.confidenceSpans:[...],
              plus body[*].provenance:{...} for verified nodes... },
  // DUPLICATE FIELDS (shell: consume snake_case variants — matches Python backend)
  confidenceSpans:    [ ...confidence spans... ],
  confidence_spans:   [ ...identical to confidenceSpans... ],
  audit_manifest: [ { claim, nodeId, z3Score, z3_score, redhatCritique, redhat_critique }, ... ],
  claims:         [ ...identical to audit_manifest... ]
}
```

Field duplication rules (shell consumption convention):
- Prefer **snake_case** keys everywhere: `confidence_spans` over `confidenceSpans`,
  `z3_score` over `z3Score`, `redhat_critique` over `redhatCritique`.
- Between list-level dupes: prefer `claims` over `audit_manifest` (aligns with
  backend naming), but treat them as identical.
- Between top-level document-internal dupes: use the copy nested inside `document.*`
  as the source of truth; the top-level `confidence_spans` list is convenience.

### Terminal sentinel

Streams end with a **bare** `data: [DONE]` line. There is no `event: done`
wrapper. Shell parsers MUST inspect `data:` lines directly and treat the
literal token `[DONE]` (NOT JSON parseable) as the stream terminator.

### Notes
- Cache peek runs BEFORE stream. If cached, `_replay_cached_compile` streams
  without consuming daily limit and without calling Claude.
- Daily compile limit is NOT consumed on cache hit.
- request_id is stable across the whole stream; use it to correlate.
- Client should cancel via stream abort (no server-side cancel route).

---

## 3. POST /api/projects/<id>/draft/redhat/stream

Stress test on an already-compiled draft. Does NOT consume daily limit.

### Request
{
  "draft_text": "<full draft text>",
  "document": { ...JDF document... },
  "z3_results": { ...optional... },
  "target_node_id": "<optional — scoped red-hat>"
}

### Pre-stream errors
| Status | Body |
|---|---|
| 400 | {error: "<validation>"} |

### SSE events
(emitted by `run_redhat_pipeline` — verify shape; same family as draft stream)
| Event | Data |
|---|---|
| status | stage progress |
| token | streaming critique |
| finding | {id, text, status:"open", suggested_fix?} |
| error | {ok:false, error, request_id} |
| [DONE] | — |

---

## 4. POST /api/runs/execute

Founder-shell pipeline. Classifies intent, detects sources, streams via
orchestrate_sourced_run, persists a run row on completion.

### Request
{
  "directive": "<user prompt>",   // required
  "workspace_id": "default",
  "source_ids": ["<id>", ...],
  "model": "gemini"
}

### Pre-stream errors
| Status | Body |
|---|---|
| 400 | {ok:false, error:"directive is required"} |
| 400 | {ok:false, error:<validation>} |

### SSE events
| Event | Data | When |
|---|---|---|
| token | {delta} or {token} | streaming draft |
| verification_complete | {data: {locks: [...]}} or {locks: [...]} | after verification |
| lock | {claim_id, lock_hash, source_id, page_coordinates, metric, lock_index} | one per grounded lock |
| complete | {ok:true, run:{...}, intent_type, router_ms, draft_text, request_id} | run persisted |
| error | {ok:false, error} | any failure |
| [DONE] | — | terminal |

### Notes
- `run` in the complete event contains: id, title, lock_count, intent_type,
  content, model, sources_used, extracted_locks, status, workspace_id.
- This is the persisted run that shows up in `GET /api/runs`.

---

## 5. GET /api/projects/<id>/jdf

Fetch JDF document. Returns latest unless `?version=` given.

### Query
?version=<int>  // optional

### Response 200
// No version specified:
{ ok:true, document: { document_id:"doc-<project_id>", meta:{project_id, title}, truth_ledger:{...} | {}, body:[...nodes...] } }

// With version:
{ ok:true, document: {...document shape above...}, version: <int> }

- `document.document_id` — `doc-<project_id>`. Always present.
- `document.meta.title` — project title. Always present (in addition to `meta.project_id`).
- `document.truth_ledger` — map of `canonical_key → numeric_value`. Always present; empty `{}` for empty/uncompiled projects.

### Errors
| Status | Body | When |
|---|---|---|
| 400 | {error:"version must be an integer"} | non-int version |
| 404 | {error:"version N not found"} | version missing |

---

## 6. GET /api/projects/<id>/history

List JDF revisions (Time Machine).

### Query
(none)

### Response 200
{ ok:true, count:<int>, history:[...revisions...], revisions:[...parallel list...] }

Note: `history` and `revisions` are both returned as parallel arrays with
the same `count`. Whether they are semantically identical or divergent is
NOT YET CONFIRMED by this contract run — requires firing against a project
with real versions saved; `count:0` on empty project, both arrays empty.

---

## Node shape (from models/jdf.py)

Every node has:
{
  "type": "paragraph" | "callout" | "table" | "image" | "signature" | "checkbox" | "section",
  "id": "<string>",
  ...type-specific fields...,
  "annotations": {
    "redhat": [{ id, text, status:"open"|"resolved"|"dismissed" }],
    "z3":     [{ id, message, status:"violation"|"pass", canonical_key }]
  }
}

Section nodes have `children: [node, ...]`.

Provenance (attached to nodes as list):
{
  source_type, source_name, url_or_doi, source_id,
  page_number, extracted_quote
}

---

## Finding update (Red-Hat Accept/Dismiss)

PATCH /api/runs/<run_id>/findings/<finding_id>

### Request
{
  "action": "accept" | "dismiss",
  "suggested_fix": "<optional, for accept>",
  "dismissal_rationale": "<required, for dismiss>"
}

### Response 200
{ ok:true, finding: { ...updated... } }

### Errors
| Status | Body | When |
|---|---|---|
| 404 | {ok:false, error:"finding not found"} | bad ids |
| 400 | {ok:false, error:"dismissal rationale required"} | dismiss without rationale |
| 400 | {ok:false, error:"action must be accept or dismiss"} | bad action |

---

## Analytics (Report tab)

| Route | Response shape (rows) |
|---|---|
| `GET /api/analytics/z3-health`          | `{ ok:true, rows:[{ audit_date:"YYYY-MM-DD", failed_locks:<int>, pass_rate_pct:<float>, passed_locks:<int>, total_checks:<int> }, ...] }` — daily Z3 pass/fail aggregates, newest-first. |
| `GET /api/analytics/redhat-critiques`   | `{ ok:true, rows:[{ critique_category:"<e.g. DRAFT_STREAM_REDHAT>", frequency:<int>, project_id:"<id>" }, ...] }` — per-project per-category red-hat finding counts. |
| `GET /api/analytics/compliance-velocity` | `{ ok:true, rows:[{ is_locked:0\|1, project_id:"<id>", title:"<project>", total_sign_offs:<int> }, ...] }` — one row per project. Sign-off pace + lock-status summary. |

---

## Runs (Pinned answers)

GET  /api/runs?workspace_id=<id>&include_findings=1
  → { ok:true, runs:[...], count:N }

Per-run object shape (from the list endpoint, verified 2026-09-13):
```
{
  id, directive, model, status, workspace_id,
  content:          { ...full JDF document... },   // NOT a string; full {meta, body, truth_ledger, document_id}
  extracted_locks:  [ {canonical_key, ...}, ... ],
  sources_used:     [ ... ],
  unanchored:       true | false,
  created_at:       "<YYYY-MM-DD HH:MM:SS>",
  updated_at:       "<YYYY-MM-DD HH:MM:SS>"
}
```

⚠️ **SHAPE DRIFT WARNING:** This listing-run object differs from the run
object emitted in `§4 POST /api/runs/execute → event: complete`, which has
fields `{id, title, lock_count, intent_type, content, model, sources_used,
extracted_locks, status, workspace_id}`:

- Listing **has** `directive, unanchored, created_at, updated_at, content:full JDF`.
- Listing **does NOT have** `title, lock_count, intent_type`.
- The `complete` event **does NOT have** `directive, unanchored, created_at, updated_at`
  and carries `content` as a **string/draft text** rather than a full JDF object.

Shell consumers MUST treat these as two distinct shapes and NOT assume they
are interchangeable; use GET /api/runs/<id> to reconcile if needed.

GET  /api/runs/<run_id>
  → { ok:true, run:{...} } | 404

POST /api/runs/<run_id>/redhat
  → { ok:true, findings:[...], count:N }

DELETE /api/runs/<run_id>
