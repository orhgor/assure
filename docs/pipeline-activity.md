# Pipeline activity — did each stage run, did its request leave the server?

Added 2026-09-28 after a customer reported that requests were not reaching
OpenRouter and nothing in the UI could show whether a stage's request had
left the server. The stage summaries (`ingest_jobs`, Parsure's
`report.execution`, the draft's `meta`) say what a stage concluded; the
**model-call ledger** says which requests were made.

## The ledger (`model_calls`, schema v37)

`services/model_calls.py` registers a litellm callback (`install()`, called by
`create_app` and by every Celery worker process) that writes one row per
completion — streamed or not, success or failure — into `model_calls`
(`db/model_calls_repository.py`): stage, project, model (provider-qualified),
HTTP status, latency, prompt/completion size, tokens, error, worker. The
Bedrock Converse path bypasses litellm and records through `record()` itself.

The stage travels *inside* the call: every call site wraps its request in
`stage_context(stage, project_id=…)` and the executors pass
`metadata={"assure": current_context()}` to litellm, which hands it back to
the callback (litellm runs the success handler on a helper thread where the
contextvar would be gone). A call with no context is attributed by the model's
stage role only when one stage uses that model; otherwise `stage` is null and
the row counts as *unattributed* — never guessed.

Stages: `parse · intake · z3 · llm_grounding · discovery · vision ·
redhat_graph · redhat_targeted · compile_draft · anchor · entailment · edit ·
redhat_multipass · compare` (`model_calls.STAGES`).

Rows are written on their own connection and committed at once, so a compile
streaming for a minute shows its request while it runs, and a rolled-back
request still leaves its evidence.

## The probe

`probe_backend()` asks the configured backend whether a request can leave at
all: OpenRouter `GET /api/v1/auth/key` (200 = key valid and network open, 401 =
key rejected, anything else = unreachable, empty key = `no_key`), Ollama
`/api/tags`; Bedrock is `not_probed`. Cached 30 s per (backend, key).
`/health` carries it as `checks.llm`; a rejected or unreachable hosted backend
degrades the box, a missing key is reported without degrading.

## The route and the panel

`GET /api/projects/<id>/pipeline-activity` joins the newest ingest job, the
newest Parsure report's `execution` ledger and the project's ledger rows into
one list in pipeline order, each stage `ran | failed | skipped | pending |
no_record` with model, call counts, last latency / HTTP status / error, plus
`llm` (backend, key hint, probe, last call, 24 h counts) and the newest `calls`.
`GET /api/model-calls` (auditors) lists every project's newest calls.

The workbench's **Pipeline activity** panel (`prototype/shell.js`, hosts
`#pipeline-activity-*`) renders it in the compile pane, the Inspector and the
Fields pane; it polls every 10 s only while a compile or ingest job is active.
A blocked probe shows "No request can leave this server: …"; zero calls in 24 h
on a hosted backend shows "No model request has been recorded". A stage with
no evidence reads *no record*; only `ran` is green.

Measured on the compose stack (2026-09-28, `final_run_assure.py`): 34 rows for
one proof project — compile 1, entailment 22, Red-Hat audit 10, Red-Hat graph 1
— all attributed by context, 0 unattributed.
