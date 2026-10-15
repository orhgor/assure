# Fable Execution Prompt V4 — Raw-First Facts Layer (FINAL; the single prompt)

**Branch:** `origin/staging-v1`, HEAD ≥ **`0993c31`** (2026-09-28 — the pin moved twice: `aa970b0` landed provenance/ocr_engine/F821-test/taxonomy_candidates/image-wrapping; `19918e1` added the model-call ledger + Pipeline activity panel; `0993c31` widened the Red-Hat wait window to 420 s. NOTE: a copy of an EARLIER version of this prompt exists in-repo at `todos/fable_execution_prompt_v4.md` — this local file is the authoritative one). **This prompt is contractual:** it binds to symbol names, behaviors, and acceptance criteria — not to line numbers or commit-specific details. A line-number appendix exists for debugging (Appendix A); if it drifts, trust the SYMBOL NAMES, re-grep, and update the appendix. Never conclude a symbol is phantom from a stale checkout (see Runbook step 0).
**Supersedes** all prior fable execution prompts (V1/V2/V3 drafts). This is the only file to execute.

---

## Part 0 — Already landed in `aa970b0` (do NOT redo; verify with the listed checks)

- **Provenance ← value_quality:** `PROVENANCE_BY_SHAPE = {"valid": 1.0, "invalid_format": 0.7, "address_fragment": 0.6, "garbage": 0.3, "header_or_label": 0.3}` (`field_extractor.py:1404`), applied at `:1812`/`:1870`. VERIFY: no `provenance_confidence: 1.0` co-occurs with non-`valid` `value_quality`.
- **ocr_engine import + F821 gate test:** `tests/test_lint_undefined_names.py` exists and passes; `ruff check prompt_matrix/ --select F821` is clean. VERIFY by running both. (Note: the test has a `pytest.skip` when ruff is missing — acceptable as an env guard, but CI must have ruff installed so the gate never silently skips.)
- **Raw layer for UNCERTAIN pages:** `field_discovery.taxonomy_candidates` (`:153`) runs every schema's label pass over an uncertain page; values → `schema_candidates` (never report fields), with per-schema provenance.
- **Image routing:** `pdf_to_parse_bundle` wraps image rasters for every upload route (the JPEG "could not be analysed" failure is fixed).

---

## Part 1 — The core architectural fix: raw-first, schema-second

**The rule:** the pipeline must never depend on document type to produce raw facts. Facts first; schema selection second; projection third; remap without loss.

### 1.1 The raw candidates layer

New module `prompt_matrix/services/raw_candidates.py`. Produces `report["raw_candidates"]` — the universe of observed facts, schema-agnostic, built for EVERY segment, typed or not (this is the delta vs. `field_discovery.applies_to`, which today only runs for schema-less types).

**Sources — ingest from every readable source already produced, not page text alone:**
- OCR text (tesseract/jdf-cli page text — always available)
- layout text (element/line structure via `page_layout`)
- table cells (`table_extraction.py` already grounds cells with spans)
- image-derived text (page rasters + vision model output when `PARSURE_VISION` is on)
- Textract key-value/forms output (when `PARSER_SCAN_BACKEND=textract` is enabled)
- discovery pairs (`field_discovery.discover_heuristic` — reuse its regex and span discipline)
Regex-first and cheap by default; model-derived sources only when already enabled — the raw layer itself spends NO additional model calls. **Model-budget note:** model calls belong to their STAGES, not to the raw layer — vision-primary consumes the vision call, grounding the grounding call, plus discovery and the Red-Hat targeted pass when they fire (see Part 3 B3: per-stage budget, ≤1 call per stage per document, all recorded in the execution ledger).

**Each candidate carries provenance only:**
```json
{"name_hint": "<slugged label>", "raw_text": "<verbatim>", "normalized_value": null,
 "label_anchor": "<label line>", "page": <int>, "node_id": "<id>", "element_id": "<id>",
 "source_span": {"start_char": <int>, "end_char": <int>},
 "source_kind": "layout_text"|"table_cell"|"image_vision"|"textract"|"discovery",
 "source_priority": <int>, "trace": "<extraction note ref>"}
```
**HARD RULES:** NO `confidence` field (decision confidence without a schema/value_shape is the V1 "garbage looks perfect" bug reborn). NO `evidence_state` (schema-layer concept; the vocabulary `FIELD_STATES`/`ROUTING_ACTIONS`/`EVIDENCE_STATES` applies only to mapped fields). Cap ~60 candidates/segment. **Model-derived candidates (source_kind `image_vision`) must be corroborated**: kept at full source priority ONLY if the value text is verified against stored page evidence (verbatim match via `llm_extraction.find_verbatim`, the same rule `field_discovery` applies at `:204`); otherwise marked `corroborated: false` and demoted BELOW deterministic sources — a hallucinated VLM read must never outrank a real OCR read.

### 1.2 Merge and precedence rules (exact — implementation must not drift here)

- **Duplicates** (same normalization-folded label — case/punctuation-insensitive — + same page + same value text): dedupe. Keep the candidate from the highest source priority AMONG corroborated candidates (per the corroboration rule in 1.1); deterministic tie-break by `(page, start_char, source_kind)`. Source priority order (documented constant in the module): `textract` > `table_cell` > `layout_text` > `discovery` regex > `image_vision` (uncorroborated). Corroborated `image_vision` outranks all.
- **Overlaps** (same/overlapping span, different text): BOTH stay in the raw pool; higher-priority source marked `preferred: true`; both are projected, and if both map to the same schema field the field outcome is `conflicting`.
- **Total determinism requirement:** every ordering decision — dedupe winner, `preferred` flag, projection order, conflict pairing — resolves by the SAME explicit key, in this exact sequence: `(source_priority, corroborated desc, page asc, start_char asc, source_kind alphabetical, raw_text lexical)`. Two engineers implementing Part 1.2 independently must produce byte-identical candidate pools and identical mapped outcomes for the same input; any tie the key cannot resolve is a bug in the key, not freedom of choice.
- **One candidate, several schema fields:** allowed — project into each independently. A candidate is NEVER consumed or destroyed by mapping.
- **Conflicts with an existing mapped field** (candidate contradicts a schema-extracted value): keep both; the mapped field gets flagged via the existing conflict machinery (`report["conflicts"]`), the candidate stays unmapped in the pool.
- **Immutability:** the raw pool is append-only. A rerun/replay may ADD candidates; it may never mutate or delete existing ones. Raw evidence outlives every wrong decision.

### 1.3 Schema projection (separate step, after type settlement)

Map candidates to the chosen schema by matching each `FieldSpec` pattern against candidate `label_anchor`/`raw_text` FIRST; only unmatched labels fall through to the existing page-text label pass. Per-field outcomes, existing vocabulary only:
- `mapped` — becomes a normal field with the full existing policy (states, routing, confidence)
- `unmapped` — stays in the raw pool (no field row)
- `conflicting` — mapped, flagged via `report["conflicts"]`
- `review_needed` — mapped but below confidence thresholds → the EXISTING `unverified` field state + `manual_review` routing. `review_needed` is a LABEL for this outcome in the projection log, never a state, never a new outcome class, never stored on the field — the field carries only existing vocabulary, and the routing applied is the SAME `manual_review` path every other low-confidence field already takes.

### 1.4 Rerun/replay = remap, not re-gate

`reextract_for_type` with a CHANGED type first re-projects the STORED raw candidates against the new schema (pure pattern matching over preserved facts), re-reading page texts only for gaps. **Ledger discipline (exact):** the AUTOMATIC in-pipeline remap records via `record_pipeline_pass` with a `pipeline:*` trigger — it does NOT count toward `replay.attempts` (the reviewer's `RERUN_MAX_ATTEMPTS` budget stays the reviewer's); only a reviewer-initiated rerun records via `record_rerun(trigger="replay")`. Recording uses the EXISTING writers with `fields_changed`/`mapping_changed` — no new trigger words, no reshaped ledger.

### 1.5 Export both layers — and survive the whole persistence chain

The export row carries `raw_candidates` alongside `fields`, `classification`, `execution`, `graph_integrity`, `replay` — via ONE `export_row(report)` helper so JSON/UI/export cannot diverge. A viewer must see "what the page says" even when the schema layer is empty.
**Persistence rule:** `raw_candidates` must survive the FULL chain — `save_report` → DB load → `public_report` → `snapshot` verify → export — **untouched**. It must NOT be stripped by persistence, report sanitization, or any `_`-prefix/whitelist filtering (`public_report` keeps non-underscore keys; `raw_candidates` is a top-level non-underscore key and every sanitizer/view/export helper must pass it through unmodified). The snapshot hash covers it (any later edit to raw candidates invalidates the snapshot — that is desired). A regression test asserts raw candidates are present and identical across the reloaded row and the export row.

## Part 2 — Anti-limit guardrails (design constraints so the fix cannot cage us)

1. **Engine abstraction:** arbitration/vision/routing code goes through the existing abstractions (`parser_router`, `cost_governance`, `textract_budget`, `services/` adapters). No direct Textract/litellm imports in pipeline code outside `prompt_matrix/lib/` and the service adapters. Swapping OCR/VLM engines must be config, not rewrite.
2. **No fixture memorization:** every fixture-driven fix must work via a GENERAL mechanism (taxonomy entry, probe factor, spec pattern). Each proof fixture class gets a paraphrased/synthetic second fixture; a fix that fires only on the original fixture text is a failure.
3. **No regression:** `scripts/validate_golden_set.py` and the full existing `tests/` suite stay green. A V3/V4 fix that degrades ANY non-V4 document class blocks the same as a red proof test.
4. **Additive extension only:** new keys on existing blocks are allowed; repurposing an existing key's meaning is not. The proof test asserts old meanings still hold.
5. **Flag freeze:** the only flags that may disable pipeline steps are the existing ones (`PARSURE_LLM_EXTRACTION`, `PARSURE_REDHAT_LLM`, `PARSURE_VISION`, `PARSER_SCAN_BACKEND`, `JDF_OCR`). No new skip flags.
6. **Vocabulary freeze:** `FIELD_STATES` / `ROUTING_ACTIONS` / `EVIDENCE_STATES` as defined in `field_extractor.py` — no new states, no new trigger words outside `RERUN_TRIGGERS`/`PIPELINE_TRIGGERS`.

---

## Part 3 — Remaining defect fixes (narrowed after `aa970b0`)

- **N1 — AMENDED CONTRACT (Fable's design is accepted):** `completion=None` no longer means "model never runs" — it selects the app model path (`llm_extraction.default_completion`, `:407`), recorded as `model_path: "app:…"` in `execution.llm_grounding` / `execution.redhat_targeted`. The requirement is now: **both passes show `ran` with `model_path` starting `app:`** on a configured backend (verified by the existing test `test_production_callers_pass_no_completion_and_the_app_model_path_still_runs_both_passes`); `failed` (not `skipped`) when no backend/key is configured, with the config named. Production callers need NO change — do NOT "fix" this by threading completion.
- **Export shape (still open):** `export_row(report)` helper in `parsure_routes.py` — export rows carry `execution`, `graph_integrity`, `replay`, `schema_candidates`/`raw_candidates`, redhat summary. Plus the `document_id: null` CI assertion (fallback exists at `export_document_id`).
- **Raw pool scope (still open):** `taxonomy_candidates` covers uncertain pages only — extend the pool to TYPED segments (the typed-but-wrong case) per Part 1, and make `reextract_for_type` remap-before-reparse.
- **G one-liners (still open):** changed fields re-verified on the NEW value or `verification_basis` says "not re-verified after grounded change" · manual replay declares `grounded: false` + reason · vestigial `redhat_draft` step deleted from `EXECUTION_STEPS` (`:1733`) and renderers · >50-page async deferral stamped `execution.z3.async_deferred: true`.
- **B1 — BLOCKING: a targeted-pass failure must not destroy Red-Hat findings.** In `run_after_parse`, `redhat_targeted_pass` is called inside the same `try/except` as the critique; if it raises (e.g. `LLMUnavailable` from a timeout), the outer handler overwrites the already-successful `report["redhat"]` block with empty findings and reports `"critique failed: …"` — erasing real evidence and misattributing the failure. FIX: wrap the `redhat_targeted_pass(...)` call in its OWN `try/except`; on failure set `execution["redhat_targeted"] = {"status": "failed", "reason": "<exception class>: <message>"}` and `log.exception` — the critique block (`report["redhat"]`, `execution["redhat_graph"]`) must remain EXACTLY as it was before the targeted pass. TEST: a completion that raises on the hinted (second) call only → `redhat_graph` keeps its findings and `status: "completed"`, `redhat_targeted.status == "failed"`, and the report still saves.
- **B2 — BLOCKING (same site): no misattributed failure reasons.** The outer handler's reason string says `"critique failed: …"` for every exception in the block. After B1's wrap, the critique handler must only report critique failures; targeted-pass failures are reported in `execution.redhat_targeted.reason` (B1). TEST: assert the two failure paths produce distinct, correctly-attributed ledger entries.
- **B3 — Model-call budget amendment (replaces the dead "one call per document" contract):** with the app-model-path design a single ingest can legitimately make up to four model calls (discovery, grounding `llm_fill_missing`, Red-Hat targeted pass, critique model check). The contract becomes a PER-STAGE budget: ≤1 call per stage per document, each recorded in the model-call ledger (`execution.*.model_path` + usage), each independently timeout-wrapped (the existing `llm_extraction._call_with_timeout`, `LLM_TIMEOUT_S = 45`). The old "one call per document" wording is retired — the ledger now counts and shows real usage per stage. TEST: a full ingest of an uncertain-page fixture makes ≤4 model calls total, each attributed to a distinct stage in the ledger.
- **Table provenance residual (still open — same bug class as the landed provenance fix):** `table_extraction.py` sets `field_rec["provenance_confidence"] = 1.0` as a CONSTANT even though the line directly above computes `field_rec["value_quality"] = fx.value_shape(spec, raw, cell["value"])`. A table cell with a garbage/header shape carries provenance 1.0. FIX (one line): derive it — `fx.PROVENANCE_BY_SHAPE.get(field_rec["value_quality"].get("quality"), 0.5)`. SWEEP: audit EVERY field-builder path (label pass, table, discovery, grounded rebuild) — provenance must come from `value_quality` via `PROVENANCE_BY_SHAPE` everywhere, never a hardcoded constant. TEST: extend the prompt-gates assertion so `provenance_confidence: 1.0` never co-occurs with non-`valid` `value_quality` across ALL builders, and add a table-cell garbage fixture to the proof suite.

---

## Part 4 — Secondary levers (only after Parts 1–3 are green; each measured, revert-if-no-improvement)

- **Taxonomy:** add `site_report` (report ref, summary/progress, contractor, client, signature, capture datetime, gps) — kills manufactured "field not found" claim rows on site/incident reports.
- **Page quality recalibration:** add grounding-success and OCR word-confidence-mean as factors in `page_quality_score`; a page the model quoted verbatim cannot score 0.07. Regression: readable-image artifact ≥ 0.5; debris scan stays low.
- **Recognition ensemble (measured, config-first):**
  - STEP 0 (zero code): `PARSER_SCAN_BACKEND=textract` + creds, `PARSURE_VISION=1` + multimodal model → re-run both artifacts, record deltas. Measurement decides the rest.
  - OCR arbitration: weak first pass → Textract `AnalyzeDocument` on the same page; arbitrate per line/word by confidence; budget-gated via `textract_budget.py`.
  - Vision-primary for image uploads: rasters already exist; vision feeds `raw_candidates` with the same provenance discipline.
  - Word-level provenance: the field's own words' OCR confidences feed `provenance_confidence`, not the page scalar.
  - Honest ceiling: forms/designed reports ≥95% realistic; crumpled/handwritten photos 85–95% — never gate on 100%.

## Part 5 — Proof, gates, and CI (unskippable)

**Fixtures (`tests/golden/proof_suite_v1/`):**
- `site_report_photo.jpg` — classification is `site_report` or `schema_mismatch+suggestion`; `raw_candidates` non-empty; real facts grounded verbatim; NO claim fields fabricated. PLUS a paraphrased second fixture of the same class (anti-memorization, Part 2 guardrail 2).
- `ocr_degraded_policy.pdf` — debris rejected by shape; raw candidates preserve labels; grounded pass recovers what the text supports.
- The existing `tests/test_end_to_end_proof.py` (TwoPassCompletion: first pass answers nothing, the Red-Hat-hinted pass answers verbatim) runs in the same suite and in CI.

**Test files:** `tests/test_proof_suite_v1.py` (new) and `tests/test_prompt_gates.py` (new — source checks: no skip/xfail in proof tests; both grounded passes run via the app model path — `execution.llm_grounding.model_path` and `execution.redhat_targeted.model_path` start with `app:` per the amended N1; no new skip flags beyond the Part 2 guardrail 5 list; `provenance_confidence: 1.0` never paired with non-valid `value_quality`; export rows never carry `document_id: null`).

**CI (`.github/workflows/ci.yml`):** a `proof-suite` job (postgres:16 service): `ruff check prompt_matrix/ --select F821` → full pytest incl. the proof suite → upload `proof_diff.json` artifact. Required branch check. Plus `scripts/run_proof_suite.sh` — the one-command local transcript.

**Acceptance rule:** a test not executed does not count; a gate weakened to pass does not count; done = F821 gate + full existing suite (no regression) + proof suite green with output shown, and the CI proof job green with `proof_diff.json` attached.

---
## Part 6 — Runbook (must follow in order)

0. **Preflight:** confirm `origin/staging-v1`, HEAD ≥ `aa970b0` (re-grep the Part 3 symbols if in doubt — checkout staleness is a problem to FIX, not a symbol phantom). Postgres up (`docker compose -f docker-compose.dev.yml up -d`, `DATABASE_URL=postgresql://assure:assure@localhost:5432/assure`). Playwright browsers if the UI proof runs. LLM backend: the unskippable gate runs on an injected fake; only the live release gate needs a real backend. Missing prerequisite → INSTALL it; only if genuinely impossible, report per step 6. A reported blocker pauses work — it never completes it.
1. **Hard-bug gate first:** `ruff check prompt_matrix/ --select F821` → zero errors. Red here blocks everything below.
2. **Pure behavior tests** (no DB): raw_candidates, merge/precedence, provenance mapping, ocr_engine import, export_row shape.
3. **DB-backed integration tests** (Postgres required): ingest/route wiring. Unavailable → report the blocker, never pretend it passed.
4. **Proof suite:** proof tests + existing end-to-end test + Playwright UI proof. Must show: `raw_candidates` on every report (both layers in export), grounded pass ran on the fake backend, no-backend = `failed` not `skipped`, ≥1 field changed via critique→rerun excluding identity fields (no re-export-only diffs), `graph_integrity` in report AND export, `document_id` stable, snapshot verifies, no `provenance_confidence: 1.0` on garbage. Red → fix code, rerun the SAME suite.
5. **Acceptance:** complete only when hard-bug gate + integration tests + proof suite + no-regression suite are all green with output shown, and the CI proof job is green with `proof_diff.json` attached.
6. **If blocked:** environment blocker (after a genuine install attempt) → report exactly (Postgres / backend / browser). Code blocker → fix and rerun the same gate. Never broaden the prompt, narrow a test, or skip the proof.
7. **Review discipline (applies to every output review, Fable's or a human's):**
   - Review only artifacts produced by a deployment running the CURRENT HEAD. If the artifact predates the current HEAD, it CANNOT be used to judge whether a fix landed — re-parse one real document on the up-to-date deployment and review that export. (Every report stamps `execution.build.commit`; check it first.)
   - Before claiming a pass "doesn't run", cite the execution-ledger entry in the artifact that says so (`execution.llm_grounding`, `redhat_targeted`, `rerun`, `vision`, … each names what ran, what failed, and why). A claim without a ledger citation is invalid; conversely, if the ledger says `ran`, engage with the output it produced.
   - Before asserting a fix "didn't land", run the gates on the current tree and show the results: `ruff check prompt_matrix/ --select F821` + `pytest tests/test_prompt_gates.py tests/test_lint_undefined_names.py -q` (seconds; all green at last audit). Reviewing a memory of the old tree instead of the current tree is how fixed bugs keep being reported as open.
   - Every quality claim must come with a field-level before/after diff (N3a rules). "It still looks old" without a diff is not a review result.

## Appendix A — Symbol map (debugging aid as of `0993c31`; informational, NOT contractual)

`v1_orchestrator.py`: `llm_fill_missing` :808 · `PIPELINE_TRIGGERS` :577 · `record_rerun` :543 · `record_pipeline_pass` :580 · `rerun_stop_rule` :524 · `RERUN_MAX_ATTEMPTS` :515 · `replay_state` :645 · `rerun_summary` :1736 · `EXECUTION_STEPS` :1733 · `redhat_graph_summary` :1938 · `redhat_targeted_pass` :1958 (guard :1988) · `assign_document_id` :1916 · `reextract_for_type` :1841
`field_extractor.py`: `FIELD_TAXONOMY` :419 · `FIELD_STATES` :73 · `ROUTING_ACTIONS` :74 · `EVIDENCE_STATES` :1644 · `value_shape` :1432 · `PROVENANCE_BY_SHAPE` :1404 · provenance applied :1812/:1870
`llm_extraction.py`: `default_completion` :407 · `find_verbatim` :145
`field_discovery.py`: `discover_heuristic` :119 · `taxonomy_candidates` :153
`parsure_routes.py`: `export_documents` :413 · `export_document_id` :459
`pdf_ingest.py`: `run_after_parse(` call :362 · `jdf_memory_routes.py`: `ocr_engine` import :27/:42, call :267
`quality_probe.py`: `page_quality_score` :789 · `vision.py`: `attach_vision` :817 / `vision_enabled` :191 · `parser_router.py`: `scan_backend` :170 · `prompt_matrix/lib/textract.py`: `analyze_document` :94

---

## Definition of Done

1. **Bugs fixed & verified:** F821 clean; provenance←value_quality landed with regression test; both grounded passes `ran` via the app model path (`model_path: "app:…"` per the amended N1); export never shows `document_id: null` or `provenance_confidence: 1.0` on garbage; B1/B2 landed — a targeted-pass failure leaves Red-Hat findings intact and is reported in `execution.redhat_targeted.reason`, never as "critique failed".
2. **Logic correct:** `raw_candidates` live on every segment from every enabled source; merge/precedence per Part 1.2 exactly (deterministic key); remap-before-reparse; existing ledger shapes preserved (extend, never reshape); no new states/flags; engine-abstraction and no-memorization guardrails hold; per-stage model budget holds (≤1 call per stage, all in the ledger — B3).
3. **Engine works as expected:** the site-report artifact classifies honestly; readable-image page_quality ≥ 0.5; the grounded pass changes ≥1 field via critique→rerun; export shows both layers; snapshot verifies; existing golden-set accuracy does not regress.
4. **All executed with output shown;** CI proof job green and required; `proof_diff.json` attached shows a real behavior change.

**If the artifact diff shows no behavior change, the task is not done — no matter what merged.**
