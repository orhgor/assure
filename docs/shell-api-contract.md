# Shell API Contract — Pipeline + Document

Scope: the five routes that drive the first shell screen
(stage viewer + document + Time Machine). Everything else
(report, compliance, sources, exports) is documented separately.

All routes require `project_ownership_required` unless noted.
All SSE responses use `Content-Type: text/event-stream`.

---

## 1. POST /api/projects/<id>/draft/stream

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
| Event | Data | When |
|---|---|---|
| status | {stage:"preflight", message:"Checking budget…", request_id} | before model call |
| status | {stage:"model", message:"Drafting with Claude…", model} | model starting |
| token | {delta: "<text>"} or raw claude frames | streaming tokens |
| usage | {input_tokens, output_tokens, model_id, task_type} | after each model call |
| status | {stage:"locks", message:"Inferring locks…"} | after draft text |
| (compiled / verified / audit_complete) | (emitted by pipeline — verify shape) | during post-processing |
| error | {ok:false, error, http_status?, request_id} | any failure |
| [DONE] | — | terminal |

### Notes
- Cache peek runs BEFORE stream. If cached, `_replay_cached_compile` streams
  without consuming daily limit and without calling Claude.
- Daily compile limit is NOT consumed on cache hit.
- request_id is stable across the whole stream; use it to correlate.
- Client should cancel via stream abort (no server-side cancel route).

---

## 2. POST /api/projects/<id>/draft/redhat/stream

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

## 3. POST /api/runs/execute

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

## 4. GET /api/projects/<id>/jdf

Fetch JDF document. Returns latest unless `?version=` given.

### Query
?version=<int>  // optional

### Response 200
// No version specified:
{ ok:true, document: { meta:{...}, body:[...nodes...] } }

// With version:
{ ok:true, document: {...}, version: <int> }

### Errors
| Status | Body | When |
|---|---|---|
| 400 | {error:"version must be an integer"} | non-int version |
| 404 | {error:"version N not found"} | version missing |

---

## 5. GET /api/projects/<id>/history

List JDF revisions (Time Machine).

### Query
(none)

### Response 200
(shape from jdf_routes.py:140 — verify)

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

GET /api/analytics/z3-health          → Z3 pass/fail aggregation
GET /api/analytics/redhat-critiques   → Red-Hat findings summary
GET /api/analytics/compliance-velocity → sign-off pace

Response shapes: verify by reading routers/analytics.py:16-60.

---

## Runs (Pinned answers)

GET  /api/runs?workspace_id=<id>&include_findings=1
  → { ok:true, runs:[...], count:N }

GET  /api/runs/<run_id>
  → { ok:true, run:{...} } | 404

POST /api/runs/<run_id>/redhat
  → { ok:true, findings:[...], count:N }

DELETE /api/runs/<run_id>
