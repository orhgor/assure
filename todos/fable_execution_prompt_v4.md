# Fable Execution Prompt V4 — Raw-First Facts Layer (FINAL; the single prompt)

**Branch:** `origin/staging-v1`, HEAD ≥ `217cf55` (2026-09-28). **This prompt is contractual:** it binds to symbol names, behaviors, and acceptance criteria — not to line numbers or commit-specific details. A line-number appendix exists for debugging (Appendix A); if it drifts, trust the SYMBOL NAMES, re-grep, and update the appendix. Never conclude a symbol is phantom from a stale checkout (see Runbook step 0).
**Supersedes** all prior fable execution prompts (V1/V2/V3 drafts). This is the only file to execute.
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
Regex-first and cheap by default; model-derived sources only when already enabled — the raw layer spends NO additional model calls. **Model-budget note:** the vision-primary path (Part 4) consumes the document's one vision call; the grounding pass consumes its one grounding call — at most those two model calls per document, each config-gated, each independently recorded in the execution ledger (`execution.vision` / `execution.llm_grounding`).

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
- `review_needed` — mapped but below confidence thresholds → the EXISTING `unverified` field state + `manual_review` routing. `review_needed` is a LABEL for this outcome in the projection log, never a state, never stored on the field — the field carries only existing vocabulary.

### 1.4 Rerun/replay = remap, not re-gate

`reextract_for_type` with a CHANGED type first re-projects the STORED raw candidates against the new schema (pure pattern matching over preserved facts), re-reading page texts only for gaps. **Ledger discipline (exact):** the AUTOMATIC in-pipeline remap records via `record_pipeline_pass` with a `pipeline:*` trigger — it does NOT count toward `replay.attempts` (the reviewer's `RERUN_MAX_ATTEMPTS` budget stays the reviewer's); only a reviewer-initiated rerun records via `record_rerun(trigger="replay")`. Recording uses the EXISTING writers with `fields_changed`/`mapping_changed` — no new trigger words, no reshaped ledger.

### 1.5 Export both layers — and survive the whole persistence chain

The export row carries `raw_candidates` alongside `fields`, `classification`, `execution`, `graph_integrity`, `replay` — via ONE `export_row(report)` helper so JSON/UI/export cannot diverge. A viewer must see "what the page says" even when the schema layer is empty.
**Persistence rule:** `raw_candidates` must survive the FULL chain — `save_report` → DB load → `public_report` → `snapshot` verify → export. Concretely: the key is non-underscore (so `public_report` keeps it), the snapshot hash covers it (any later edit to raw candidates invalidates the snapshot — that is desired), and NO serializer/view/export helper may drop it. A regression test asserts raw candidates are present and identical across the reloaded row and the export row.


## Part 2 — Anti-limit guardrails (design constraints so the fix cannot cage us)

1. **Engine abstraction:** arbitration/vision/routing code goes through the existing abstractions (`parser_router`, `cost_governance`, `textract_budget`, `services/` adapters). No direct Textract/litellm imports in pipeline code outside `prompt_matrix/lib/` and the service adapters. Swapping OCR/VLM engines must be config, not rewrite.
2. **No fixture memorization:** every fixture-driven fix must work via a GENERAL mechanism (taxonomy entry, probe factor, spec pattern). Each proof fixture class gets a paraphrased/synthetic second fixture; a fix that fires only on the original fixture text is a failure.
3. **No regression:** `scripts/validate_golden_set.py` and the full existing `tests/` suite stay green. A V3/V4 fix that degrades ANY non-V4 document class blocks the same as a red proof test.
4. **Additive extension only:** new keys on existing blocks are allowed; repurposing an existing key's meaning is not. The proof test asserts old meanings still hold.
5. **Flag freeze:** the only flags that may disable pipeline steps are the existing ones (`PARSURE_LLM_EXTRACTION`, `PARSURE_REDHAT_LLM`, `PARSURE_VISION`, `PARSER_SCAN_BACKEND`, `JDF_OCR`). No new skip flags.
6. **Vocabulary freeze:** `FIELD_STATES` / `ROUTING_ACTIONS` / `EVIDENCE_STATES` as defined in `field_extractor.py` — no new states, no new trigger words outside `RERUN_TRIGGERS`/`PIPELINE_TRIGGERS`.

---

## Part 3 — Blocking defect fixes (small, verified, must land with Part 1)

- **Provenance:** `field_extractor.py` sets `field["provenance_confidence"] = 1.0` unconditionally for found fields. Replace with the `value_quality` mapping (garbage/header_or_label → 0.3, invalid_format → 0.7, address_fragment → 0.6, valid → 1.0). Regression test: `provenance_confidence: 1.0` may NEVER co-occur with a non-`valid` `value_quality`.
- **Completion threading:** `pdf_ingest.py` and `routers/substrate.py` call `run_after_parse(...)` without `completion` — the grounded targeted pass is a silent no-op (guard `if not llm and completion is None`). Build the real callable from the app's model path; pass it in both callers. Guard test: no `run_after_parse(` call in either file without `completion=`.
- **ocr_engine (hard bug):** `jdf_memory_routes.py` calls `ocr=ocr_engine()` with no import — NameError → 500 on scanned uploads. Add the guarded import. CI gate: `ruff check prompt_matrix/ --select F821` = zero errors (ruff F821, NOT full pyflakes — full pyflakes carries ~173 pre-existing noise findings).
- **document_id:** export rows never carry `document_id: null` (the `export_document_id` fallback exists — verify the deployed path uses it; CI assertion).
- **Export shape:** Part 1.5's `export_row` helper (execution, graph_integrity, replay, raw_candidates, redhat summary).
- **G one-liners:** changed fields re-verified on the NEW value or `verification_basis` says "not re-verified after grounded change" · manual replay declares `grounded: false` + reason · vestigial `redhat_draft` step deleted from `EXECUTION_STEPS` and renderers · >50-page async deferral stamped `execution.z3.async_deferred: true`.

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

**Test files:** `tests/test_proof_suite_v1.py` (new) and `tests/test_prompt_gates.py` (new — source checks: no skip/xfail in proof tests; both production callers pass `completion=`; no new skip flags beyond the Part 2 guardrail 5 list; `provenance_confidence: 1.0` never paired with non-valid `value_quality`; export rows never carry `document_id: null`).

**CI (`.github/workflows/ci.yml`):** a `proof-suite` job (postgres:16 service): `ruff check prompt_matrix/ --select F821` → full pytest incl. the proof suite → upload `proof_diff.json` artifact. Required branch check. Plus `scripts/run_proof_suite.sh` — the one-command local transcript.

**Acceptance rule:** a test not executed does not count; a gate weakened to pass does not count; done = F821 gate + full existing suite (no regression) + proof suite green with output shown, and the CI proof job green with `proof_diff.json` attached.

---
## Part 6 — Runbook (must follow in order)

0. **Preflight:** confirm `origin/staging-v1`, HEAD ≥ `217cf55` (re-grep the Part 3 symbols if in doubt — checkout staleness is a problem to FIX, not a symbol phantom). Postgres up (`docker compose -f docker-compose.dev.yml up -d`, `DATABASE_URL=postgresql://assure:assure@localhost:5432/assure`). Playwright browsers if the UI proof runs. LLM backend: the unskippable gate runs on an injected fake; only the live release gate needs a real backend. Missing prerequisite → INSTALL it; only if genuinely impossible, report per step 6. A reported blocker pauses work — it never completes it.
1. **Hard-bug gate first:** `ruff check prompt_matrix/ --select F821` → zero errors. Red here blocks everything below.
2. **Pure behavior tests** (no DB): raw_candidates, merge/precedence, provenance mapping, ocr_engine import, export_row shape.
3. **DB-backed integration tests** (Postgres required): ingest/route wiring. Unavailable → report the blocker, never pretend it passed.
4. **Proof suite:** proof tests + existing end-to-end test + Playwright UI proof. Must show: `raw_candidates` on every report (both layers in export), grounded pass ran on the fake backend, no-backend = `failed` not `skipped`, ≥1 field changed via critique→rerun excluding identity fields (no re-export-only diffs), `graph_integrity` in report AND export, `document_id` stable, snapshot verifies, no `provenance_confidence: 1.0` on garbage. Red → fix code, rerun the SAME suite.
5. **Acceptance:** complete only when hard-bug gate + integration tests + proof suite + no-regression suite are all green with output shown, and the CI proof job is green with `proof_diff.json` attached.
6. **If blocked:** environment blocker (after a genuine install attempt) → report exactly (Postgres / backend / browser). Code blocker → fix and rerun the same gate. Never broaden the prompt, narrow a test, or skip the proof.

## Appendix A — Symbol map (debugging aid as of `217cf55`; informational, NOT contractual)

`v1_orchestrator.py`: `llm_fill_missing` :808 · `PIPELINE_TRIGGERS` :577 · `record_rerun` :543 · `record_pipeline_pass` :580 · `rerun_stop_rule` :524 · `RERUN_MAX_ATTEMPTS` :515 · `replay_state` :645 · `rerun_summary` :1734 · `EXECUTION_STEPS` :1731 · `redhat_graph_summary` :1936 · `redhat_targeted_pass` :1956 (guard :1986) · `assign_document_id` :1914 · `reextract_for_type` :1839
`field_extractor.py`: `FIELD_TAXONOMY` :419 · `FIELD_STATES` :73 · `ROUTING_ACTIONS` :74 · `EVIDENCE_STATES` :1644 · `value_shape` :1432 · `mark_low_quality_page` :2003 · `apply_decision_policy` :2269 · unconditional `provenance_confidence = 1.0` :1809
`parsure_routes.py`: `export_documents` :413 · `export_document_id` :459
`pdf_ingest.py`: `run_after_parse(` call :362 (no `completion=`) · `jdf_memory_routes.py`: `ocr_engine()` call :267 (no import)
`quality_probe.py`: `page_quality_score` :789 · `field_discovery.py`: `discover_heuristic` :104 · `vision.py`: `attach_vision` :811 / `vision_enabled` :186 · `parser_router.py`: `scan_backend` :170 · `prompt_matrix/lib/textract.py`: `analyze_document` :94

---

## Definition of Done

1. **Bugs fixed & verified:** F821 clean; provenance←value_quality landed with regression test; both callers pass `completion=`; export never shows `document_id: null` or `provenance_confidence: 1.0` on garbage.
2. **Logic correct:** `raw_candidates` live on every segment from every enabled source; merge/precedence per Part 1.2 exactly; remap-before-reparse; existing ledger shapes preserved (extend, never reshape); no new states/flags; engine-abstraction and no-memorization guardrails hold.
3. **Engine works as expected:** the site-report artifact classifies honestly; readable-image page_quality ≥ 0.5; the grounded pass changes ≥1 field via critique→rerun; export shows both layers; snapshot verifies; existing golden-set accuracy does not regress.
4. **All executed with output shown;** CI proof job green and required; `proof_diff.json` attached shows a real behavior change.

**If the artifact diff shows no behavior change, the task is not done — no matter what merged.**
