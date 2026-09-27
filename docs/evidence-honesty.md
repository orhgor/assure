# Evidence honesty

Date: 2026-09-27. Scope: the evidence the shell shows beside a claim — page,
excerpt, proof, confidence — and the verifiers that produce it. Companion to
`docs/anti-claims.md` ("Evidence honesty (2026-09-27)"); the entailment gate and
claim policy are documented in `docs/verification.md`.

## The rule

| Field | Is | Never |
|---|---|---|
| page | the page the quote was found on (`--- Page N ---` marker, or the producer's located coordinates), or `null` | `1` by default; the page of a stray word; `page_count` |
| excerpt / quote | verbatim source text located by search (`llm_extraction.find_verbatim` comparison: whitespace collapsed, case folded), or `null` | the first N characters of the file; the claim's own text; the metric name |
| proof | a real Z3 run's output, or `{"status": "not_run", "reason": …}` | an SMT-LIB fragment with `; status: SAT` written without a solver |
| score / confidence | `null` | a fixed number (0.92 / 0.5 / 0.15) a UI reads as confidence |
| numeric check | a status word: `matches_lock` — a figure in the claim equals the value locked for a metric the claim names; `contradicts_lock` — the claim names a locked metric and states a different figure; `no_lock` — the claim names no locked metric or carries no figure. Figures are read with their suffix (`12 million`, `$12M`, `3.5bn`, `40k`) as well as bare (`truth_engine.claim_numbers`) | a number; a contradiction against a lock the claim never names |
| `verified_at` | the check's timestamp when a figure was checked against a lock | `now()` on every node |
| verifier verdict | `VERBATIM_MATCH` (claim found verbatim, passage at its offset) / `INSUFFICIENT_EVIDENCE` ("heuristic overlap is not verification") / a groundrails verdict | `grounded` on 40 % word overlap; `stamped`; `PASS` from an empty solver |
| Red-Hat quote | the source sentence as the source writes it, re-found verbatim (`quote_verbatim: true`), or `null` when the finding quotes nothing | a paraphrase presented as the source |

## Shell-facing keys (old → new)

Every old key is kept so a reader that looks for it finds an explicit `null`;
the new key carries the meaning.

| Where | Old | New |
|---|---|---|
| `node.meta.provenance` | `confidence: 0.92` (default) | `confidence: null`, `numeric_consistency: "matches_lock"\|"no_lock"\|"contradicts_lock"` |
| `node.meta.provenance` | `excerpt` = claim text when no quote | `excerpt` = `extracted_quote` or located sentence, else `null` |
| `node.meta.provenance` | `verified_at` = now | `verified_at` = timestamp only when a figure was checked, else `null` |
| `node.meta.provenance` | `rule: "ledger_check"` | `rule: "<key> == <value>"` or `null` |
| `meta.confidenceSpans[]` / `node.meta.confidenceSpans[]` | `score: 0.92 / 0.5 / 0.25` | `score: null`, `numeric_consistency` |
| macro appendix rows | `z3Score`, `z3_score` numbers | both `null`, `numeric_consistency` |
| `GET /api/locks/<hash>/evidence` | `page_number: 1`, `excerpt` fallback, `z3_proof` string | `page_number: <int>\|null`, `excerpt: <str>\|null`, `z3_proof: <str>\|{"status":"not_run","reason"}` |
| `extracted_locks[]` | `page_coordinates: {page: 1, …}` | `page_coordinates: {…}\|null`, `origin: "draft"` |
| `/api/runs/execute` locks | `status: grounded\|amber`, `confidence_score: <float>` | `status: grounded\|unverified`, `confidence_score: null`, `verdict`, `reason` |
| `runs.status` | `stamped` | `anchored` (orchestrator) / `unverified` (auto-compiler); `contradiction` and `draft` unchanged |
| inquire `truth_check` | `{status: "PASS", detail: "Z3 Verified"}` with no figures | `{status: "SKIPPED", detail: "no figures to check"}`; with figures `PASS\|VIOLATION\|TIMEOUT` plus `checked: <n>` |
| inquire stream | — | new `anchoring` frame `{node_id, anchored, citations, entailment, sources}`; new `redhat_dropped` frame when a critique's quote is not verbatim |
| inquire `redhat_annotation.annotation` | — | `quote`, `quote_verbatim`, `evidence_kind` |
| compile `redhat` frame | `status: "skipped", skip_reason: "no Red-Hat audit was requested for this compile"` | `status: "scheduled", task_id` or `status: "skipped", skip_reason` (auto off / no broker / empty draft) |
| Red-Hat findings (multipass telemetry, node audit) | — | `quote`, `quote_verbatim`, `evidence_kind: "quoted"\|"observation"`; dropped findings and empty quotes counted in `notes` |

Shell note (for `prototype/shell.js`): `spanChannel.wrap` paints `conf-red` for
`sp.score === null` because `null > 0.8` and `null >= 0.4` are both false. Read
`numeric_consistency` instead and leave a `no_lock` span unpainted; the
`data-score` attribute will read `"null"`.

## Red-Hat after a compile

`ASSURE_REDHAT_AUTO` (default on). The compile stream (`routers/draft.py`,
`_redhat_auto_frame`) builds the compiled tree from the draft text and calls
`routers/redhat_routes.schedule_redhat_after_compile(project_id, run_id=None,
current_jdf=…)`, which resets the project's telemetry to `pending`, enqueues
`tasks/redhat.run_redhat_multipass_task` against the previous stored revision
and records the task id. `GET /api/projects/<id>/redhat/status` therefore moves
`idle → pending → pass1_running → … → complete` without a click. Without a
broker the telemetry says `error: no task broker configured` and the frame says
`skipped`. The persist step may call `schedule_redhat_after_compile(project_id,
run_id)` again with the persisted tree; `signals._enqueue_multipass` bumps the
generation and revokes the earlier task, so the later call supersedes it.

Both Red-Hat prompts (node-scoped `run_redhat_audit`, multipass Pass 1 / Pass 2)
list every anchored quote of the node — `node.provenance[].extracted_quote`,
all of them — and ask the model to copy a source sentence exactly when a finding
rests on it. `services/redhat_verbatim.verbatim_gate` keeps a finding only when
each quote it makes (its `quote` field, or a “…” / «…» / "…" span in its
content) is re-found verbatim in those sentences; otherwise the finding is
dropped and the drop is written to `notes` (multipass) or returned as an error
entry with `code: "redhat_quote_not_verbatim"` (node audit). A finding that
quotes nothing passes with `quote: null, quote_verbatim: false`.

Every finding carries **`evidence_kind`**: `"quoted"` only when a verbatim
quote exists, `"observation"` when the model gave none. The shell must render
an observation as the model's remark about the text, never as evidence from
the source. The multipass prompts make `quote` a required key (`""` for an
observation); how many came back empty is counted in `notes` ("N finding(s)
returned no quote and are recorded as observations"). Cache rows and telemetry
written before the label existed are read as `observation` unless they carry a
verbatim quote (`redhat_verbatim.with_evidence_kind`).

**Generations.** `signals._enqueue_multipass` bumps the project's audit
generation for every new compile. A pass whose generation is no longer current
owns nothing: `tasks/redhat._telemetry_update` compares the generation before
every write and drops it with a log line, and a pass that finds itself stale
returns `{"ok": false, "stale": true}` without writing `error:
stale_generation` over the newer generation's `pending` (live run 2026-09-27).

## Legacy verifiers

`services/verifier`, `services/orchestrator`, `services/auto_compiler`,
`tasks/compile_tasks` are the founder-workbench paths (`/api/runs`,
`/api/runs/execute`, `assure.safe_compile_and_verify`); the shell does not call
them. Their endpoints keep working; their verdicts are now what they can
support: a verbatim match, or insufficient evidence with the reason. The
compile task's grounding heuristic still gates its redraft retry, but the
result says `verified: false` and its Z3 entry says `not_run`.

## Known remaining gap (not this change)

`models/jdf.attach_substrate_provenance_to_tree` writes `page_number` from the
matched sentence's page and, when the matcher has none, from the row's
`page_count`. A page count is not the page a sentence was found on; it should
become `""`/`null` (owner: models/jdf anchoring).
