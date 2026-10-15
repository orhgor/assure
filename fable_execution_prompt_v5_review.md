# Fable Execution Prompt V5 — Review Protocol & Residual Fix (companion to V4)

**Branch:** `origin/staging-v1`, HEAD ≥ `33d9f79` (2026-09-28). This is the COMPANION to `fable_execution_prompt_v4.md`: V4 builds; V5 verifies and fixes what the first post-V4 review surfaced. V4's gates remain in force.

**Context:** a post-V4 code review flagged six issues ("no raw_candidates, no execution, provenance 1.0, document_id null, completion not threaded, replay unproven"). Audited against `33d9f79`: **five of six were stale** — they describe the pre-`aa970b0` deployment, not the current code (raw layer wired at parse + export; `export_row` carries all blocks; `assign_document_id` + fallback on every path; app-model-path grounding per amended N1; replay ledger + `proof_diff.json` all exist and the prompt gates run green). **One issue is REAL** and is fixed by this prompt. The deeper finding is procedural: the review compared an OLD artifact against the NEW code — which always reads as "still broken." This prompt makes that impossible.

---

## Part R — The one residual defect (REAL, fix first)

**R1. Table-cell provenance is still a constant `1.0`.**
`table_extraction.py` (the `field_rec` build): it correctly computes `field_rec["value_quality"] = fx.value_shape(spec, raw, cell["value"])` on the line above, then sets `field_rec["provenance_confidence"] = 1.0` as a CONSTANT. A table cell whose shape is garbage/header carries provenance 1.0 — the exact V1 bug, surviving in the table path that the `PROVENANCE_BY_SHAPE` fix did not cover.
- FIX (one line): `field_rec["provenance_confidence"] = fx.PROVENANCE_BY_SHAPE.get(field_rec["value_quality"].get("quality"), 0.5)`.
- SWEEP: audit EVERY field-builder path for the same constant (`grep -n 'provenance_confidence' prompt_matrix/services/ -r`); every one must derive from `PROVENANCE_BY_SHAPE[shape]` or carry an explicit reason. Verify the unconditional `valid` at the llm-grounded rebuild path is only reached AFTER a passing shape judgment.
- TEST: a table cell with a garbage-shaped value → `provenance_confidence ≤ 0.7` and `value_quality.quality != "valid"`; a valid cell → 1.0. Add to `test_prompt_gates.py`: `provenance_confidence: 1.0` never co-occurs with non-`valid` `value_quality` ACROSS ALL field builders (label pass, table, discovery, grounded rebuild).

---

## Part V — The review protocol (process, not code)

**V1. Review only artifacts built on a deployment running HEAD.**
Before any code review of output quality: confirm the deployment revision equals `origin/staging-v1` HEAD (the artifact should carry a build/commit stamp — if absent, add one: `execution.build = {"commit": "<short sha>", "branch": "staging-v1"}` stamped in `run_after_parse`). Reviewing an old export against new code always reads "still broken" and wastes a review cycle. A review of a pre-HEAD artifact is INVALID — re-parse first.

**V2. Re-parse one real document on that deployment; review THAT export.**
Never review a cached/old artifact. The protocol: upload one real document (start with the two known artifacts: the site-report JPG and the OCR-degraded policy PDF) through the production ingest route, then review the export produced seconds earlier. Diff it against the pre-V4 export — the N3a rules apply (identity/timestamp/wrapper changes are not change).

**V3. Read the execution ledger BEFORE claiming a pass "doesn't run".**
Every stage now names what ran, what failed, and why: `execution.llm_grounding` (status/model_path/fields_grounded), `execution.redhat_graph`, `execution.redhat_targeted` (hints/changed/reason), `execution.rerun`, `execution.vision`, `execution.discovery`, `execution.z3` (+ `async_deferred`), `execution.projection`, `execution.raw_pool` stats. A claim of "X doesn't run" MUST cite the ledger entry that says so — a claim without a ledger citation is rejected. Conversely: if the ledger says `ran`, the reviewer must engage with the output it produced, not re-assert absence.

**V4. Run the gates before asserting a fix "didn't land".**
Three commands, seconds each, all green on `33d9f79` at audit time:
```
ruff check prompt_matrix/ --select F821          # → All checks passed!
pytest tests/test_prompt_gates.py -q             # → 9 passed
pytest tests/test_lint_undefined_names.py -q     # → 1 passed
```
A reviewer asserting "the provenance fix isn't landed" (or any Part 0/3 item) without first running these — and without citing a failing line of the current tree — is reviewing a memory, not the code.

**V5. Review output = diff, not verdict.**
Every quality review must produce a field-level before/after diff (N3a rules) naming which fix produced which change. "It still looks old" without the diff is rejected; "policy_number changed from debris to AP-2025-0001 via redhat_targeted.changed" is accepted.

---

## Definition of Done (V5)

1. R1 landed: table-cell provenance derived from `PROVENANCE_BY_SHAPE`; the all-builders sweep done; the cross-builder gate test green.
2. V1 stamp landed: every report carries `execution.build.commit`.
3. The review protocol (V1–V5) is the process for every subsequent output review — reviewer claims must cite ledger entries and current-tree gate results.
4. Gates re-run green after R1: F821 + prompt gates + lint test.

**This prompt is done when the next artifact review cannot find a stale claim in it.**
