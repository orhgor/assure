# Fable Execution Plan: Assure Parsure V1 — Comprehensive

**Status:** Ready for execution  
**Goal:** Real solid satisfactory output with enterprise look and execution  
**Scope:** All missing points from prior work + new additions + UI connections + enterprise polish  

---

## Context: What We're Fixing

The system has **capacity** (LAYA, Z3, rerun infra, table extraction, value quality assessment, visual probing, Red-Hat code, etc.) but **no execution** of that capacity in the main pipeline. The `build_report_timed` pipeline does a single-pass label extraction and outputs whatever it has — no LAYA, no Z3, no rerun, no LLM grounding.

The output shows:
- `laya.status: "not_started"` — LAYA never ran
- `verification.z3_status: "not_run"` — Z3 never ran
- `replay.attempts: 0, improved: false, history: []` — no rerun happened
- `provenance_confidence: 1.0` on garbage values — confidence doesn't reflect value quality
- `verification_confidence: 0.85` on null fields (in stale JSON) — confidence doesn't reflect actual state
- No `grounding_quote`, no `grounding_span` — LLM grounding not run
- `redhat_findings: "pending"` — no Red-Hat findings

**The system is not lying on purpose — it's just not executing.** The pieces exist, the pipeline just doesn't call them.

**Additionally, staging-v1 already has some fixes** that the stale JSON doesn't reflect:
- `_empty_field` correctly sets `verification_confidence: None`, `field_state: "not_found"`, `routing_action: "field_not_found"`, `review_required: False` for not-found fields
- `review_summary` separates `fields_not_found` from `fields_review`
- `value_shape` function assesses value quality
- `mark_low_quality_page` prevents auto-acceptance

**But staging-v1 still has one major code issue:** `provenance_confidence = 1.0` in `build_found_field` (line ~1610) for ALL found fields, regardless of value quality. This is still broken.

**And staging-v1 is missing the execution model** — LAYA, Z3, rerun, LLM grounding are not wired in.

**And staging-v1 has no picture handling, no table-aware extraction, no schema evolution.**

This plan addresses ALL of these.

---
## Fable Execution Framework

**This is not a feature list. This is a pipeline enforcement exercise.**

Fable's job is to make the pipeline enforce truth, or refuse to export. Not to add more capabilities for the sake of capabilities.

### Non-Negotiables

These are the rules Fable must follow. No exceptions.

**1. Do not create new states.**

- Use the existing `FIELD_STATES`, `ROUTING_ACTIONS`, and `evidence_state` vocabulary only.
- `FIELD_STATES = ("accepted", "partial", "unverified", "disputed", "rejected", "not_found")`
- `ROUTING_ACTIONS = ("none", "manual_review", "adjudicator_queue", "compliance_review", "retry_parsure", "replay_later", "field_not_found")`
- `evidence_state` values: `"found_suspect"`, `"not_on_document"`, `"schema_mismatch"`, `"found_verified"`, `"found_unverified"`
- If a state or action is not in these constants, it does not exist. Do NOT invent new ones.
- This is a hidden threat if ignored: creating a second incompatible state system.

**2. Wire existing capabilities into `build_report_timed`.**

- LAYA / Red-Hat: must run in the pipeline, not sit in separate files
- Z3 verification: must run in the pipeline, not sit in separate files
- Rerun loop: must run in the pipeline, not sit in separate files
- LLM grounding: must run in the pipeline, not sit in separate files
- The pipeline must call these. Not just "they exist somewhere."

**3. Fix the visible quality bug.**

- `provenance_confidence` must reflect `value_quality`.
- Garbage values must NOT look perfect (`provenance_confidence: 1.0`).
- This is the most visible output issue. Fix it first.

**4. Preserve artifact truth.**

- Canonical snapshot: every report gets a hash
- Artifact hash gate: if hash doesn't match, export is refused
- Export refusal on mismatch: do NOT export corrupted or tampered reports
- JSON / UI / export / replay must agree: if they don't, refuse to export

**5. Treat the end-to-end proof test as the gate.**

- Red-Hat runs → verified
- Rerun happens → verified
- At least one field changes → verified
- Node pointers remain correct → verified
- Grounding appears in output → verified
- The updated report is reflected everywhere (JSON, UI, export, replay) → verified

**If the end-to-end proof test does not pass, the plan is not complete.** Not "mostly complete." Not "ready for testing." Not complete.

### Success Means

Success is NOT "the docs say the pipeline should work."

Success is **"the output is visibly better because the pipeline proves it changed."**

- Before: `provenance_confidence: 1.0` on garbage, `laya.status: "not_started"`, `z3_status: "not_run"`, `replay.attempts: 0`
- After: `provenance_confidence: 0.3` on garbage, `laya.status: "completed"` with findings, `z3_status: "pass"` with violations, `replay.attempts: 1` with changed fields

The difference is visible in the output. Not in the code. Not in the docs. In the output.

### Execution Order

Fable executes in this order:

1. **Fix the visible quality bug** (Phase 1.1) — garbage no longer looks perfect
2. **Fix the state-machine vocabulary** (Phase 1.2) — use ONLY existing constants
3. **Wire LLM grounding** (Phase 2.1) — enable grounding quotes/spans
4. **Wire LAYA/Red-Hat** (Phase 2.2) — LAYA findings in output
5. **Wire Z3** (Phase 2.3) — Z3 verification in output
6. **Wire rerun loop** (Phase 2.4) — iterative improvement
7. **Add artifact integrity gates** (Phase 2.6 / Part 2.6) — refuse to export on mismatch
8. **Add graph/node integrity** (Part 2.7) — orphan detection, negative evidence, stable IDs
9. **Add grounding propagation** (Part 2.9) — where grounding is created, stored, rendered
10. **Add final run script** (Part 2.10) — concrete, executable proof
11. **Execute final run** — prove the pipeline works

If the final run passes, the plan is complete. If it fails, identify the gap and remediate. Repeat until pass.

### What Fable Must NOT Do

- Do NOT create new states or actions
- Do NOT add features that don't contribute to the end-to-end proof
- Do NOT consider a phase "done" until the final run proves it
- Do NOT skip the end-to-end proof test
- Do NOT export unless the pipeline enforces truth

### What Fable Must Do

- Wire existing capabilities into `build_report_timed`
- Fix the visible quality bug
- Preserve artifact truth
- Run the end-to-end proof test
- Prove the output changed

---

## Part 1: The Output Quality Fixes (Phase 1)


These are small, high-impact fixes to the existing code. They address the most visible output quality issues.

### 1.1 Fix `provenance_confidence` in `build_found_field`

**File:** `prompt_matrix/services/field_extractor.py`, `build_found_field` function (around line 1610)

**Current code:**
```python
field["provenance_confidence"] = 1.0  # Line ~1610 — sets 1.0 for ALL found fields
```

**Problem:** Garbage values (policy_number with header text, insured_name with address) show `provenance_confidence: 1.0` which is completely wrong.

**Fix:**
```python
# Use value_shape() result to adjust provenance_confidence
value_quality = field.get("value_quality") or "valid"  # from value_shape assessment
if value_quality == "valid":
    field["provenance_confidence"] = 1.0
elif value_quality == "invalid_format":
    field["provenance_confidence"] = 0.7
elif value_quality in ("garbage", "header_or_label"):
    field["provenance_confidence"] = 0.3
elif value_quality == "address_fragment":
    field["provenance_confidence"] = 0.6
else:
    field["provenance_confidence"] = 0.8  # default for unknown quality
```

**Why:** `value_shape()` already assesses value quality. We just need to use its result. This fixes the most visible output issue — garbage values no longer look perfect.

**Test:** Run on the same documents that produced garbage values (policy_number with header text, insured_name with address). Verify provenance_confidence is low (0.3-0.6) for garbage values.

---

### 1.2 Fix `field_state` / `routing_action` for found-but-unparseable fields

**File:** `prompt_matrix/services/field_extractor.py`, `build_found_field` (around line 1645)

**Current code:**
```python
field["field_state"], field["routing_action"], field["review_required"] = "unverified", "manual_review", True
```

**Problem:** Found-but-unparseable fields (value found under label but couldn't be parsed) get the same treatment as unverified fields. They should be distinguished.

**Fix:**
```python
if field.get("value_quality") in ("garbage", "header_or_label", "address_fragment"):
    # Value found but is garbage/header/address — suspect
    # USE EXISTING FIELD_STATES AND ROUTING_ACTIONS ONLY
    # FIELD_STATES = ("accepted", "partial", "unverified", "disputed", "rejected", "not_found")
    # ROUTING_ACTIONS = ("none", "manual_review", "adjudicator_queue", "compliance_review", "retry_parsure", "replay_later", "field_not_found")
    # evidence_state values: "found_suspect", "not_on_document", "schema_mismatch", "found_verified", "found_unverified"
    field["field_state"] = "unverified"  # VALID field_state
    field["routing_action"] = "manual_review"  # VALID routing_action
    field["evidence_state"] = "found_suspect"  # VALID evidence_state (this is where "suspect" goes)
    field["review_required"] = True
elif field.get("field_state") == "not_found":
    # Already handled by _empty_field — do NOT change
    pass
else:
    # Normal found field — check if it should be accepted
    if field.get("confidence", 0) >= 0.8 and field.get("value_quality") == "valid":
        field["field_state"] = "accepted"  # VALID field_state
        field["routing_action"] = "none"  # VALID routing_action
        field["evidence_state"] = "found_verified"  # VALID evidence_state
        field["review_required"] = False
    else:
        field["field_state"] = "unverified"  # VALID field_state
        field["routing_action"] = "manual_review"  # VALID routing_action
        field["evidence_state"] = "found_unverified"  # VALID evidence_state
        field["review_required"] = True
```

**CRITICAL: Use ONLY existing vocabulary.**

- `FIELD_STATES = ("accepted", "partial", "unverified", "disputed", "rejected", "not_found")` — from `field_extractor.py` line 70
- `ROUTING_ACTIONS = ("none", "manual_review", "adjudicator_queue", "compliance_review", "retry_parsure", "replay_later", "field_not_found")` — from `field_extractor.py` line 71
- `evidence_state` values: `"found_suspect"`, `"not_on_document"`, `"schema_mismatch"`, `"found_verified"`, `"found_unverified"` — from existing codebase

**DO NOT create new states.** The plan previously contained WRONG vocabulary (`found_suspect`, `review_suspect`, `found`, `review_unverified`, `auto_accepted`) that is NOT in the existing constants. That was a hidden threat. This fix corrects it.

**Note:** Staging-v1 already has `_empty_field` fix for NOT-FOUND fields (sets `field_state: "not_found"`, `routing_action: "field_not_found"`, `review_required: False`, `evidence_state: "not_on_document"`). This fix is for FOUND-but-suspect fields.

**Test:** Verify found-but-unparseable fields get `field_state: "unverified"`, `routing_action: "manual_review"`, `evidence_state: "found_suspect"`. Do NOT verify for `field_state: "found_suspect"` or `routing_action: "review_suspect"` — those are WRONG.


```

**Why:** Distinguishes "found+garbage" from "found+unverified" from "found+clean". Improves routing accuracy.

**Note:** Staging-v1 already has `_empty_field` fix for NOT-FOUND fields (sets `field_state: "not_found"`, `routing_action: "field_not_found"`, `review_required: False`). This fix is for FOUND-but-suspect fields.

**Test:** Verify found-but-unparseable fields get `field_state: "unverified"`, `routing_action: "manual_review"`, `evidence_state: "found_suspect"`. Do NOT verify for `field_state: "found_suspect"` or `routing_action: "review_suspect"` — those are WRONG.


---

### 1.3 Verify `review_summary` correctly separates not-found from review

**File:** `prompt_matrix/services/v1_orchestrator.py`, `review_summary` function (around line 425)

**Check:** Does the function correctly separate `fields_not_found` from `fields_review`?

**Expected behavior:**
- `fields_not_found`: fields where nothing was found under the label (field_state: "not_found")
- `fields_review`: fields where something was found but needs review (field_state: "unverified" with review_required=True, evidence_state: "found_suspect" or "found_unverified")


**If correct:** Confirm in Phase 6 verification. No code change needed.

**If incorrect:** Fix to separate correctly. The staging-v1 code claims to have this fix, but verify it actually works in the output.

**Test:** Run on a document with both not-found fields and review-required fields. Verify `review_summary` has correct counts for each.

---

### 1.4 Verify JSON serialization includes all field metadata

**Check:** Does the output JSON include:
- `routing_action`
- `provenance_confidence`
- `grounding_quote`
- `grounding_span`
- `value_quality`
- `field_state`
- `verification_confidence`

**If any are missing:** Add them to the serialization. They should be in the field dict but may be dropped during JSON serialization.

**If present but empty:** That's fine — they'll be populated when LLM grounding runs (Phase 2.1).

**Test:** Check a sample output JSON for all the above fields.

---

### 1.5 Add classification confidence to output (if not already)

**Check:** Does the output JSON include `classification.confidence`?

**If not:** Add it. The classification already computes confidence — it should be in the output.

**If classification confidence is low (< 0.5):** The document_type should reflect uncertainty. Either:
- Keep the document_type but show low confidence, OR
- Mark the type as uncertain (e.g., `"auto_policy?"` or add `"uncertain": true`)

**Test:** Verify classification confidence is in the output and reflects actual uncertainty.

---

### 1.6 Fix `matched_keywords` inconsistency

**Check:** Does `classification.matched_keywords` match what the basis says?

**Problem:** JSON shows `matched_keywords: []` but basis says "1 hit". This is either:
- A bug in classification logic (matched_keywords not populated correctly)
- A stale JSON issue (fixed in staging-v1)

**If still broken:** Fix the classification logic to populate `matched_keywords` correctly.

**If fixed in staging-v1:** Verify in Phase 6.

**Test:** Verify `matched_keywords` is consistent with the basis.

---

## Part 2: The Execution Model Wiring (Phase 2)

This is the BIG fix. The system has LAYA, Z3, rerun, and LLM grounding capabilities, but they're not called in the main pipeline. This phase wires them in.

### 2.0 Key Distinction: Wire Existing Pieces, Don't Invent New Ones

**The plan assumes the branch is missing LAYA, Z3, rerun, and grounding entirely. This is wrong.**

Parts of these already exist in staging-v1:
- LAYA/Red-Hat: `founder_redhat.py`, `inquire_stream.py`, `redhat_routes.py`, `sandbox.py`
- Z3 verification: Z3 infrastructure exists (referenced in verification field, verification_version, etc.)
- Rerun: `rerun_stop_rule()`, `record_rerun()`, `replay_state()`, `reextract_for_type()` all exist
- LLM grounding: `llm_extraction.py`, `completion` parameter, `llm_fill_missing`, `classify_with_model` all exist

**The real task is NOT to invent everything from scratch.** The real task is to:

1. **Wire the existing pieces into the actual production path** — call them in `build_report_timed`, not just let them sit in separate files
2. **Stop stale defaults from winning** — `completion=None`, `verification=None`, `laya=(intake or {}).get("laya")` all pass through None
3. **Prove the output changed end-to-end** — run a single test that shows Red-Hat critique happens, rerun is triggered, node/field output changes, and the updated artifact is reflected in JSON/UI/export/replay together

**This plan's job:** Take what exists in staging-v1, wire it into the production pipeline, stop the stale defaults, and prove it works. Not add more features for the sake of features.

**The pipeline must enforce truth, or refuse to export.** Not just "add more capabilities."
### 2.1 Make `completion` non-None in production (LLM grounding)

**File:** `prompt_matrix/services/v1_orchestrator.py`, the caller of `build_report_timed`

**Current:** `completion: Any = None` in `build_report_timed`. Production leaves it None — LLM pass skipped.

**Problem:** Without `completion`, the LLM grounding pass doesn't run. This means:
- No `llm_fill_missing` (LLM fills missing fields)
- No `classify_with_model` for uncertain classification
- No grounding quotes/spans
- No improved confidence from LLM

**Fix:**
```python
# In the caller of build_report_timed (e.g., run_after_parse or similar):
from prompt_matrix.services import llm_extraction as lx

# Create the completion callable
def completion(prompt: str | list[dict], **kwargs) -> dict:
    """LLM call for grounding. Use the app's model path."""
    return lx.call_llm(prompt, **kwargs)  # or however the app calls LLM

# Pass to build_report_timed
report = build_report_timed(
    ...,
    completion=completion,  # NOT None
    ...
)
```

**What this enables:**
- `llm_fill_missing` in `extract_segment_fields` — LLM fills fields that label pass missed
- `classify_with_model` for uncertain classification — LLM helps classify uncertain docs
- Grounding quotes and spans in extracted fields — LLM provides evidence for extracted values
- Improved confidence from LLM assessment

**Test:** Run on a document. Verify:
- `grounding_quote` and `grounding_span` are populated for extracted fields
- Missing fields are filled by LLM
- Classification uses LLM for uncertain docs

**Note:** This is a wiring change, not a new capability. The LLM extraction code (`llm_extraction.py`) already exists. We just need to pass `completion` to it.

---

### 2.2 Wire LAYA/Red-Hat into the pipeline

**File:** `prompt_matrix/services/v1_orchestrator.py`, `build_report_timed` (after extraction, before output)

**Current:** `"laya": (intake or {}).get("laya")` — just passes through None.

**Problem:** LAYA capability exists (`founder_redhat.py`, `inquire_stream.py`, `redhat_routes.py`) but is not called in the pipeline.

**Fix:**
```python
# After extraction loop, before report dict creation:

# Prepare text for LAYA review
report_text = "\n\n".join(texts) if texts else ""

# Run LAYA/Red-Hat review
laya_result = None
redhat_findings = "not_run"
if completion and llm_enabled:  # Only if LLM is available (Phase 2.1)
    try:
        from prompt_matrix.services.founder_redhat import run_adversarial_redhat
        laya_result = run_adversarial_redhat(
            run_id=report_id,  # or some identifiers
            workspace_id=project_id,
        )
        # Or use the existing LAYA function if different
        # laya_result = run_laya_review(report_text, project_id)
    except Exception as e:
        log.warning("LAYA review failed: %s", e)
        laya_result = None

if laya_result:
    report["laya"] = laya_result.get("laya", {})
    report["redhat_findings"] = laya_result.get("findings", "pending")
    report["redhat_status"] = "completed"
else:
    report["laya"] = (intake or {}).get("laya") or {"status": "not_started"}
    report["redhat_findings"] = "not_run"
    report["redhat_status"] = "not_run"
```

**What this adds to output:**
- `laya.status`: `"completed"` with actual findings (not `"not_started"`)
- `redhat_findings`: actual findings (not `"pending"`)
- `redhat_status`: `"completed"` (not `"not_run"`)

**Test:** Run on a document. Verify:
- `laya.status` is `"completed"` with findings
- `redhat_findings` has actual content
- `redhat_status` is `"completed"`

**Note:** LAYA may be expensive (calls another LLM). Consider:
- Running LAYA asynchronously (fire and forget, poll for results)
- Running LAYA only for high-value documents
- Caching LAYA results

For now, run synchronously to verify it works.

---

### 2.3 Wire Z3 verification into the pipeline

**File:** `prompt_matrix/services/v1_orchestrator.py`, `build_report_timed` (after extraction, before output)

**Current:** `verification` parameter is None, `z3_status: "not_run"` in output.

**Problem:** Z3 verification capability exists but is not called in the pipeline.

**Fix:**
```python
# After extraction loop, before report dict creation:

z3_result = None
if z3_enabled:  # Determine if Z3 should run (config, document type, etc.)
    try:
        # Use existing Z3 infrastructure
        from prompt_matrix.services import z3_verification
        z3_result = z3_verification.verify_fields(fields, document_type)
    except Exception as e:
        log.warning("Z3 verification failed: %s", e)
        z3_result = None

if z3_result:
    report["verification"]["z3_status"] = z3_result.get("status", "unknown")
    report["verification"]["z3_violation_count"] = z3_result.get("violation_count", 0)
    if z3_result.get("violations"):
        report["conflicts"].extend(z3_result["violations"])
    report["verification"]["z3_details"] = z3_result.get("details")
else:
    report["verification"]["z3_status"] = "not_run"
    report["verification"]["z3_violation_count"] = None
```

**What this adds to output:**
- `verification.z3_status`: `"pass"` or `"violation"` with actual status (not `"not_run"`)
- `verification.z3_violation_count`: actual count (not `None`)
- `conflicts`: Z3 violations added to report conflicts

**Test:** Run on a document. Verify:
- `verification.z3_status` is `"pass"` or `"violation"`
- `verification.z3_violation_count` is a number
- `conflicts` includes Z3 violations if any

**Note:** Z3 may be expensive. Consider:
- Running Z3 only for compliance-bound fields
- Running Z3 asynchronously
- Caching Z3 results

For now, run synchronously to verify it works.

---

### 2.4 Wire the rerun loop into the pipeline

**File:** `prompt_matrix/services/v1_orchestrator.py`, `build_report_timed` (after initial extraction + verification)

**Current:** `rerun_stop_rule()`, `record_rerun()`, `replay_state()`, `reextract_for_type()` exist but aren't called. `replay.attempts: 0, improved: false, history: []` in output.

**Problem:** No iterative improvement. Single pass extraction, no retry for low-confidence fields.

**Fix:**
```python
# After initial extraction + verification, before report creation:

# Identify low-confidence fields
def identify_low_confidence_fields(fields, threshold=0.6):
    """Fields with confidence below threshold that could benefit from rerun."""
    low_conf = []
    for f in fields:
        conf = f.get("verification_confidence") or f.get("provenance_confidence") or 0.5
        quality = f.get("value_quality") or "valid"
        if (conf < threshold or quality in ("garbage", "invalid_format")):
            low_conf.append(f)
    return low_conf

# Rerun loop
replay = {
    "attempts": 0,
    "improved": False,
    "history": [],
    "final_low_confidence_count": 0,
}

initial_fields = list(fields)  # copy
replay["history"].append({
    "attempt": 0,
    "approach": "initial_label_pass",
    "low_confidence_count": len(identify_low_confidence_fields(initial_fields)),
    "field_samples": [f["name"] for f in identify_low_confidence_fields(initial_fields)[:5]],
})

low_confidence_fields = identify_low_confidence_fields(fields)
attempts = 0
no_improvement_count = 0
previous_low_count = len(low_confidence_fields)

# Rerun up to max attempts
while low_confidence_fields and attempts < 3 and no_improvement_count < 2:
    attempts += 1
    
    # Try re-extraction with different approach
    # Approach options: different parser, different segment boundaries, LLM fill, etc.
    approach = next_approach(attempts)  # implement this
    
    # Re-extract low-confidence fields
    reextracted = reextract_low_confidence(
        low_confidence_fields,
        approach=approach,
        completion=completion,
        project_id=project_id,
    )
    
    # Replace or merge reextracted fields
    for old_field in fields:
        for new_field in reextracted:
            if old_field.get("name") == new_field.get("name") and old_field.get("segment") == new_field.get("segment"):
                # Replace if new field has better confidence
                old_conf = old_field.get("verification_confidence") or old_field.get("provenance_confidence") or 0
                new_conf = new_field.get("verification_confidence") or new_field.get("provenance_confidence") or 0
                if new_conf > old_conf:
                    # Replace old with new
                    idx = fields.index(old_field)
                    fields[idx] = new_field
    
    # Recheck low confidence
    low_confidence_fields = identify_low_confidence_fields(fields)
    current_low_count = len(low_confidence_fields)
    
    # Check improvement
    if current_low_count < previous_low_count:
        no_improvement_count = 0
        replay["improved"] = True
    else:
        no_improvement_count += 1
    
    previous_low_count = current_low_count
    
    # Record attempt
    replay["history"].append({
        "attempt": attempts,
        "approach": approach,
        "low_confidence_count": current_low_count,
        "field_samples": [f["name"] for f in low_confidence_fields[:5]],
        "improvement": current_low_count < previous_low_count,
    })

replay["attempts"] = attempts
replay["final_low_confidence_count"] = len(low_confidence_fields)

# Update report
report["replay"] = replay
---

## Part 2.5: What Already Exists (Mandatory Reading Before Execution)

**This section separates what's already in the codebase from what's missing.**

Do NOT assume everything is missing. Verify each item before implementing.

### Already Implemented (DO NOT re-implement)

1. **not_found handling** — `_empty_field` correctly sets `field_state: "not_found"`, `routing_action: "field_not_found"`, `review_required: False`, `evidence_state: "not_on_document"`. This is in staging-v1 and working.

2. **value_quality assessment** — `value_shape()` function exists and assesses value quality (valid/invalid_format/garbage/header_or_label/address_fragment). This is in staging-v1.

3. **FIELD_STATES and ROUTING_ACTIONS constants** — Defined in `field_extractor.py` line 70-71:
   - `FIELD_STATES = ("accepted", "partial", "unverified", "disputed", "rejected", "not_found")`
   - `ROUTING_ACTIONS = ("none", "manual_review", "adjudicator_queue", "compliance_review", "retry_parsure", "replay_later", "field_not_found")`
   - **DO NOT create new states.** Use these constants.

4. **evidence_state** — Used in the codebase: `found_suspect`, `not_on_document`, `schema_mismatch`, `found_verified`, `found_unverified`. This is in staging-v1.

5. **review_summary separation** — `review_summary` function separates `fields_not_found` from `fields_review`. This is in staging-v1.

6. **mark_low_quality_page** — Prevents auto-acceptance for low-quality pages. This is in staging-v1.

7. **Snapshot/integrity work** — Some snapshot and integrity work exists in the codebase. Verify what exists before adding more.

8. **Red-Hat graph code** — `redhat_graph.py` exists with graph operations. This is in staging-v1.

9. **rerun routes / replay support** — `rerun_stop_rule()`, `record_rerun()`, `replay_state()`, `reextract_for_type()` exist. This is in staging-v1.

### Partially Implemented (May need completion)

1. **LAYA/Red-Hat** — Functions exist (`founder_redhat.py`, `inquire_stream.py`, `redhat_routes.py`, `sandbox.py`) but are NOT wired into the main pipeline. Need to wire them in.

2. **Z3 verification** — Referenced in the codebase but need to verify the actual implementation exists and is functional.

3. **LLM grounding** — `llm_extraction.py`, `completion` parameter, `llm_fill_missing`, `classify_with_model` exist but `completion=None` in production. Need to wire `completion` into the pipeline.

4. **Rerun loop** — Infrastructure exists but is NOT wired into the pipeline. Need to wire it in.

### Missing (Need to implement)

1. **Vision model integration** — Does NOT exist. Need to implement from scratch (Phase 5).

2. **Picture-to-structured-data extraction** — Does NOT exist. Need to implement (Phase 5.2).

3. **Picture-specific fields in taxonomy** — Does NOT exist. Need to add (Phase 5.4).

4. **Schema registry** — Does NOT exist. Need to implement (Phase 4.1).

5. **Dynamic field discovery** — Does NOT exist. Need to implement (Phase 4.2).

6. **JDF handling for pictures** — Does NOT exist. Need to implement (Part 9.5).

### Must NOT Be Changed

1. **FIELD_STATES and ROUTING_ACTIONS constants** — These are defined in the codebase. Do NOT create new states or actions. Use the existing constants.

2. **_empty_field handling** — This is already correct. Do NOT change it.

3. **review_summary separation** — This is already correct. Verify it works, but do NOT re-implement.

4. **value_shape function** — This is already correct. Use it, do NOT re-implement.

5. **Existing field state transitions** — The code at lines 1645, 1827, 2093, 2096, 2100, 2104, 2108, 2112, 2116, 2133, 2136 already handles field state transitions. Do NOT create conflicting transitions.

---

## Part 2.6: Hard Artifact Consistency Gate

**The pipeline must refuse to export unless ALL of the following are true.**

### Refusal Conditions

The `safe_export()` function MUST refuse to export if ANY of these conditions are true:

1. **Artifact hash mismatch**
   - `report["artifact_hash"]` does not match `canonical_snapshot(report)`
   - **Refuse:** "Artifact hash mismatch — report may be corrupted"

2. **Graph integrity failure**
   - `report["graph_integrity"]["integrity_score"]` < 0.9
   - OR `report["graph_integrity"]["orphan_fields"]` > 0
   - OR `report["graph_integrity"]["orphan_negatives"]` > 0
   - **Refuse:** "Graph integrity failure — report has orphans or integrity issues"

3. **Snapshot hash differs**
   - The snapshot computed at export time differs from the snapshot computed at report creation time
   - **Refuse:** "Snapshot hash mismatch — report changed after creation"

4. **Rerun/replay state disagrees**
   - `report["replay"]` does not match the replay state in the system
   - OR `report["fields"]` have different node_ids or grounding than the replay state
   - **Refuse:** "Replay/replay state disagreement — report may be out of sync"

5. **JSON/UI/export agreement failure**
   - `verify_agreement(report, ui_state, export_state, replay_state)` returns issues
   - **Refuse:** "Agreement violation: {issues}"

### Implementation

```python
class ExportRefused(Exception):
    """Raised when export is refused due to integrity checks."""
    pass

def safe_export(report: dict, ui_state: dict, replay_state: dict, 
                export_state: dict | None = None, 
                snapshot_at_creation: str | None = None) -> dict:
    """Export report only if all integrity checks pass. Refuse otherwise."""
    
    # Check 1: Artifact hash
    if not verify_artifact_integrity(report):
        raise ExportRefused("Artifact hash mismatch — report may be corrupted")
    
    # Check 2: Graph integrity
    graph = report.get("graph_integrity") or {}
    if graph.get("integrity_score", 1.0) < 0.9:
        raise ExportRefused(f"Graph integrity failure — score {graph.get('integrity_score')} < 0.9")
    if graph.get("orphan_fields", 0) > 0:
        raise ExportRefused(f"Graph integrity failure — {graph.get('orphan_fields')} orphan fields")
    if graph.get("orphan_negatives", 0) > 0:
        raise ExportRefused(f"Graph integrity failure — {graph.get('orphan_negatives')} orphan negatives")
    
    # Check 3: Snapshot hash
    if snapshot_at_creation:
        current_snapshot = canonical_snapshot(report)
        if current_snapshot != snapshot_at_creation:
            raise ExportRefused("Snapshot hash mismatch — report changed after creation")
    
    # Check 4: Rerun/replay state agreement
    if replay_state:
        issues = verify_node_address_consistency(report, replay_state)
        if issues:
            raise ExportRefused(f"Replay state disagreement: {'; '.join(issues)}")
    
    # Check 5: JSON/UI/export agreement
    if ui_state:
        issues = verify_agreement(report, ui_state, export_state or {}, replay_state or {})
        if issues:
            raise ExportRefused(f"Agreement violation: {'; '.join(issues)}")
    
    # All checks passed — safe to export
    return export_report(report)
```

### When to Call safe_export

- When exporting to file (Phase 8.5)
- When exporting to API response
- When exporting to UI
- When exporting to replay
- Whenever the report leaves the pipeline

### Testing

- Test that valid reports export successfully
- Test that reports with hash mismatch are refused
- Test that reports with graph integrity failure are refused
- Test that reports with snapshot mismatch are refused
- Test that reports with replay disagreement are refused
- Test that reports with agreement failure are refused

---

## Part 2.7: Explicit Graph/Node Integrity Contract

**These are the invariants that MUST hold for the graph to be valid.**

### Invariant 1: Stable document_id
- `document_id` is set once at report creation
- `document_id` NEVER changes across revisions, reruns, or exports
- If a revision is created, it references the same `document_id`

### Invariant 2: Stable node pointers
- Each field has a unique `node_id` (e.g., `field-{document_id}-{field_name}`)
- `node_id` is set at field creation and NEVER changes across revisions or reruns
- If a field is re-extracted, it keeps the same `node_id`
- If a field is removed, its `node_id` is retired (not reused)

### Invariant 3: Node-address consistency
- The `node_id` in the report must match the `node_id` in the replay state
- The `node_id` in the report must match the `node_id` in the export
- If any mismatch, the export is refused (see Part 2.6)

### Invariant 4: Orphan detection
- Every node in the JDF tree must be addressed by a field or negative evidence node
- If a node is not addressed, it is flagged as an orphan
- Orphans reduce graph integrity score
- If orphan count > 0, graph integrity is degraded (but not necessarily refused)

### Invariant 5: Negative evidence nodes
- For every field with `field_state == "not_found"`, there is a corresponding negative evidence node
- Negative evidence nodes have the same `document_id` and `segment` as the field
- Negative evidence nodes have their own `node_id` (e.g., `ne-{document_id}-{field_name}`)

### Invariant 6: Mixed-bundle page coverage
- For mixed bundles, each document segment must have its pages classified
- `page_classification` must cover all pages in the bundle
- `bundle_summary` must accurately reflect the bundle composition

### Invariant 7: Field-to-node mapping
- Each field references exactly one JDF node via `node_id`
- The JDF node must exist and be of the correct type (paragraph, table, picture, signature, etc.)
- If a field references a non-existent node, it is flagged

### Verification

These invariants are verified in:
- `graph_integrity` computation (Phase 9.2)
- `orphan_detection` (Phase 9.3)
- `node_address_consistency` check (Phase 9.4)
- `safe_export` refusal conditions (Part 2.6)
- End-to-end proof test (Phase 16.1)

---

## Part 2.8: Single End-to-End Proof Test

**This is ONE concrete test that proves the entire pipeline works.**

### Test: End-to-End Red-Hat + Rerun + Grounding Flow

```python
def test_end_to_end_execution():
    """
    Prove that:
    1. Red-Hat runs and produces findings
    2. Rerun is triggered based on findings
    3. Field output changes after rerun
    4. Node pointers remain correct (no drift)
    5. UI/export/replay all show the same updated state
    6. Export is refused if any integrity check fails
    """
    
    # === STEP 1: Create initial report ===
    initial_report = build_report_timed(
        project_id="test-project",
        bundle=test_bundle,
        verification=None,
        filename="test.pdf",
        result=test_result,
        job_id="test-job",
        intake=test_intake,
        completion=completion_fn,  # LLM grounding enabled
        tree=test_tree,
    )
    
    # === STEP 2: Verify initial state ===
    assert initial_report["laya"]["status"] == "not_started"
    assert initial_report["verification"]["z3_status"] == "not_run"
    assert initial_report["replay"]["attempts"] == 0
    assert initial_report["artifact_hash"] is not None
    assert initial_report["document_id"] is not None
    
    # Snapshot the initial report for later comparison
    initial_snapshot = canonical_snapshot(initial_report)
    initial_fields = {f["name"]: f for f in initial_report["fields"]}
    initial_node_ids = {f["name"]: f.get("node_id") for f in initial_report["fields"]}
    
    # === STEP 3: Run LAYA/Red-Hat ===
    laya_result = run_laya_review(initial_report, project_id)
    
    # Verify LAYA ran
    assert laya_result["laya"]["status"] == "completed"
    assert len(laya_result.get("findings", [])) > 0, "Red-Hat should find something"
    
    # === STEP 4: Capture LAYA findings ===
    laya_findings = laya_result.get("findings", [])
    laya_affected_fields = set()
    for finding in laya_findings:
        # Determine which field the finding affects
        field_name = extract_field_name_from_finding(finding)
        if field_name:
            laya_affected_fields.add(field_name)
    
    assert len(laya_affected_fields) > 0, "Red-Hat should affect at least one field"
    
    # === STEP 5: Trigger rerun based on LAYA findings ===
    rerun_result = trigger_rerun(
        report=initial_report,
        laya_findings=laya_findings,
        project_id=project_id,
        completion=completion_fn,
    )
    
    # Verify rerun happened
    assert rerun_result["replay"]["attempts"] >= 1, "Rerun should have happened"
    
    # === STEP 6: Verify field output changed ===
    final_fields = {f["name"]: f for f in rerun_result["fields"]}
    
    # Check that affected fields changed
    changed_fields = []
    for field_name in laya_affected_fields:
        if field_name in initial_fields and field_name in final_fields:
            initial = initial_fields[field_name]
            final = final_fields[field_name]
            if (initial.get("value") != final.get("value") or
                initial.get("confidence") != final.get("confidence") or
                initial.get("field_state") != final.get("field_state")):
                changed_fields.append(field_name)
    
    assert len(changed_fields) > 0, \
        f"Rerun should have changed at least one field. " \
        f"Affected: {laya_affected_fields}, Changed: {changed_fields}"
    
    # === STEP 7: Verify node pointers remained correct (NO DRIFT) ===
    for field_name in initial_node_ids:
        if field_name in final_fields:
            initial_nid = initial_node_ids[field_name]
            final_nid = final_fields[field_name].get("node_id")
            assert initial_nid == final_nid, \
                f"Node pointer drifted for {field_name}: {initial_nid} -> {final_nid}"
    
    # === STEP 8: Verify graph integrity ===
    assert rerun_result["graph_integrity"]["integrity_score"] >= 0.9, \
        f"Graph integrity score too low: {rerun_result['graph_integrity']['integrity_score']}"
    assert rerun_result["graph_integrity"]["orphan_fields"] == 0, \
        f"Orphan fields found: {rerun_result['graph_integrity']['orphan_fields']}"
    
    # === STEP 9: Verify artifact hash ===
    assert verify_artifact_integrity(rerun_result), "Artifact hash mismatch"
    
    # === STEP 10: Verify snapshot consistency ===
    final_snapshot = canonical_snapshot(rerun_result)
    # Snapshot should be different from initial (because fields changed)
    assert final_snapshot != initial_snapshot, "Snapshot should have changed"
    
    # === STEP 11: Verify JSON/UI/export/replay agreement ===
    ui_state = simulate_ui_state(rerun_result)
    export_state = simulate_export_state(rerun_result)
    replay_state = rerun_result  # replay is the source of truth
    
    agreement_issues = verify_agreement(
        json_state=rerun_result,
        ui_state=ui_state,
        export_state=export_state,
        replay_state=replay_state,
    )
    assert len(agreement_issues) == 0, \
        f"Agreement issues: {agreement_issues}"
    
    # === STEP 12: Verify export is NOT refused ===
    try:
        exported = safe_export(
            report=rerun_result,
            ui_state=ui_state,
            replay_state=replay_state,
            snapshot_at_creation=final_snapshot,
        )
        assert exported is not None, "Export should succeed"
    except ExportRefused as e:
        pytest.fail(f"Export refused when it should have succeeded: {e}")
    
    # === STEP 13: Verify export IS refused when integrity fails ===
    # Create a tampered report
    tampered = deepcopy(rerun_result)
    tampered["fields"][0]["value"] = "TAMPERED"
    # Recompute hash incorrectly (or don't recompute)
    tampered["artifact_hash"] = "wrong_hash"
    
    try:
        safe_export(tampered, ui_state, replay_state, snapshot_at_creation=final_snapshot)
        pytest.fail("Export should have been refused for tampered report")
    except ExportRefused:
        pass  # Expected
    
    # === STEP 14: Verify grounding propagation ===
    for field_name in changed_fields:
        final_field = final_fields[field_name]
        if final_field.get("source") == "llm" or final_field.get("method") == "llm_fill":
            assert final_field.get("grounding_quote") is not None, \
                f"LLM field {field_name} should have grounding quote"
            assert final_field.get("grounding_span") is not None, \
                f"LLM field {field_name} should have grounding span"
            assert final_field.get("grounding_model") is not None, \
                f"LLM field {field_name} should have grounding model"
    
    return {
        "initial_report": initial_report,
        "laya_result": laya_result,
        "rerun_result": rerun_result,
        "changed_fields": changed_fields,
        "agreement_issues": agreement_issues,
        "passed": True,
        "test_name": "end_to_end_execution",
    }
```

### What This Test Proves

1. **Red-Hat runs** — `laya_result["laya"]["status"] == "completed"` and findings exist
2. **Rerun happens** — `rerun_result["replay"]["attempts"] >= 1`
3. **Field output changes** — `len(changed_fields) > 0`
4. **Node pointers remain correct** — no drift between initial and final node_ids
5. **UI/export/replay agree** — `len(agreement_issues) == 0`
6. **Export works when integrity is good** — `safe_export` succeeds
7. **Export is refused when integrity fails** — tampered report is refused
8. **Grounding propagation works** — LLM fields have grounding_quote, grounding_span, grounding_model

### Test Requirements

- A test document that Red-Hat can find something on
- A test document with at least one field that can be improved by rerun
- A working `completion_fn` (LLM grounding)
- A working `run_laya_review` function
- A working `trigger_rerun` function
- A working `safe_export` function
- A working `verify_agreement` function

### Fallback

If any of the requirements cannot be met, the test cannot run. Document the gap and skip the test. The plan can still proceed without this test, but the risk of "execution without effect" remains.

---

## Part 2.9: First-Class Grounding Propagation

**This clarifies exactly how grounding_quote and grounding_span flow through the system.**

### Where Grounding is Created

Grounding is created in TWO places:

1. **LLM extraction (llm_extraction.py):**
   - When `llm_fill_missing` or `classify_with_model` is called
   - The LLM returns a response that includes the extracted value AND the grounding quote/span
   - This is parsed and the grounding_quote/grounding_span are added to the field
   - This is the PRIMARY source of grounding

2. **Label-pass extraction (field_extractor.py):**
   - When a label-pass extraction finds a value, it may include a grounding quote (the text around the label)
   - This is secondary grounding (less reliable than LLM grounding)
   - The grounding_quote is the text snippet that contains the value

### How Grounding is Stored in the Report

Grounding is stored in each field as:
- `grounding_quote`: The text that supports the extracted value
- `grounding_span`: The location of the grounding quote in the document (page, start, end)
- `grounding_model`: The model that provided the grounding (e.g., "gemini-pro")
- `grounding_timestamp`: When the grounding was created

These fields are serialized into the report in `build_report_timed` (Phase 11.3).

### How Grounding Affects Confidence and Review

- **High-confidence grounding (LLM with high confidence):** Field can be auto-accepted
- **Low-confidence grounding (LLM with low confidence):** Field needs review
- **No grounding (label-pass only):** Field is unverified, needs review
- **Garbage value with grounding:** Grounding may still be present, but value_quality is "garbage" — field needs review

Grounding does NOT automatically fix value_quality. A field can have grounding_quote but still have value_quality "garbage" if the value itself is garbage.

### How Grounding is Rendered in the UI


### Overview
  - Show the grounding_span (page number, clickable to highlight in document)
  - Show the grounding_model (which model provided the grounding)
  - Show the grounding_timestamp (when it was created)
- Clicking on the grounding_quote highlights the text in the document viewer
- Clicking on the grounding_span navigates to the page and highlights the span

### Grounding Propagation Through Rerun

- When a field is re-extracted, the new extraction may have new grounding
- The old grounding is replaced with the new grounding
- The grounding_timestamp is updated to the rerun time
- The grounding_model may change if a different model was used

### Grounding and Export

- Grounding fields are serialized in the export (Phase 11.3)
- The export filename includes key data points (Phase 8.5)
- The export is refused if grounding is inconsistent with the report (Part 2.6)

---
## Part 2.10: Final Run Execution (Fable's Proof Run)

### Critical Execution Verification

**This is the most important section in the plan. Read it carefully.**

The plan describes wiring Red-Hat/LAYA, Z3, rerun, and grounding into `build_report_timed`. That is necessary but NOT sufficient.

**The wiring is "more complete on paper" until the end-to-end proof test passes.**

**The end-to-end proof test (Part 2.8 / Phase 16.1 / Part 2.10) is the CRITICAL execution verification.** It proves that:

1. **Red-Hat actually runs** — not just "wired in the code"
2. **Rerun actually happens** — not just "the loop exists"
3. **Field values actually change** — not just "the loop runs"
4. **Node pointers stay correct** — not just "node_id is set"
5. **JSON/UI/export/replay all reflect the updated report** — not just "the report is built"

**Without this test passing, the plan is "more complete on paper" without producing visible output change.**

**This is the difference between:**
- "We wired Red-Hat into the code" (paper completeness)
- "Red-Hat ran, found something, triggered rerun, changed a field value, and the updated value appears in the JSON, UI, export, and replay" (real execution)

**The end-to-end proof test is the single most important verification in the entire plan.** Everything else (Phase 1 fixes, Phase 2 wiring, Phase 3-16 features) is preparation for this test. If this test doesn't pass, nothing else matters.

**Do NOT consider the plan complete until this test passes.**

**Do NOT consider a phase "done" until the end-to-end proof test passes with that phase's contribution visible.**

**Example:**
- Phase 2.2 (wire LAYA) is NOT done until the end-to-end proof test shows Red-Hat ran and affected a field.
- Phase 2.4 (wire rerun) is NOT done until the end-to-end proof test shows rerun happened and changed a field.
- Phase 2.1 (wire grounding) is NOT done until the end-to-end proof test shows grounding_quote/grounding_span in the output.

**The end-to-end proof test is the gate. Everything else is preparation.**

---

### Overview

### Overview

Fable executes this final run after all phases are implemented. The run:

1. **Executes the end-to-end proof test** (from Part 2.8 / Phase 16.1)
2. **Produces a verifiable result** — pass or fail with specific assertions
3. **If pass:** Assure works as planned. The result is real.
4. **If fail:** Identifies gaps and missing points. Remediation is done. The run is repeated until pass.

### Prerequisites

Before executing the final run, ensure:

- [ ] All phases implemented (Phase 1 through Phase 16, plus Part 2.5-2.9)
- [ ] LLM grounding enabled (`completion` is non-None)
- [ ] LAYA/Red-Hat wired into pipeline
- [ ] Z3 verification wired into pipeline
- [ ] Rerun loop wired into pipeline
- [ ] Artifact integrity gates implemented (Part 2.6)
- [ ] Graph/node integrity contract implemented (Part 2.7)
- [ ] Grounding propagation implemented (Part 2.9)
- [ ] Safe export implemented (Part 2.6)
- [ ] Export filename convention implemented (Phase 8.5)
- [ ] Test data available (see Test Data Requirements below)
- [ ] Required functions available and callable (see Function Availability below)

### Test Data Requirements

The final run requires specific test data:

**Test Document 1: Red-Hat + Rerun Test Document**
- A document that Red-Hat can find findings on (e.g., a document with compliance issues, errors, or ambiguities)
- A document with at least one field that has low confidence and can be improved by rerun
- Preferably a real document from the test suite, or a synthetic document designed to trigger Red-Hat findings

**Test Document 2: Baseline Test Document**
- A document with good extraction results (for comparison)
- Used to verify that the pipeline doesn't break good extractions

**Test Document 3: Edge Case Document (optional)**
- A document with edge cases (mixed bundle, no text, pictures, tables, etc.)
- Used to verify edge case handling

**If test data doesn't exist:** Create synthetic test documents or use existing documents from the test suite. Document what test data was used.

### Function Availability Checklist

Before executing the final run, verify these functions exist and are callable:

- [ ] `build_report_timed()` — in `v1_orchestrator.py`
- [ ] `completion_fn()` — LLM grounding function (wired in Phase 2.1)
- [ ] `run_laya_review()` or `run_adversarial_redhat()` — LAYA/Red-Hat function (wired in Phase 2.2)
- [ ] `trigger_rerun()` — rerun function (wired in Phase 2.4)
- [ ] `safe_export()` — safe export function (Part 2.6)
- [ ] `verify_agreement()` — agreement check function (Part 2.6)
- [ ] `canonical_snapshot()` — snapshot function (Part 2.6)
- [ ] `verify_artifact_integrity()` — artifact hash check function (Part 2.6)
- [ ] `verify_node_address_consistency()` — node address check function (Part 2.6 / Phase 9.4)
- [ ] `export_report()` — export function (Phase 8.4)
- [ ] `build_export_filename()` — export filename function (Phase 8.5)

**If any function doesn't exist:** Implement it first, then proceed with the final run.

### Final Run Script

Create a file `test_final_run.py` with the following content:

```python
#!/usr/bin/env python3
"""
Fable Final Run: End-to-End Proof Test
Proves that Assure works as planned.
"""

import sys
import os
sys.path.insert(0, "/tmp/assure_repo")

from prompt_matrix.services.v1_orchestrator import build_report_timed
from prompt_matrix.services.founder_redhat import run_adversarial_redhat
from prompt_matrix.services.field_extractor import canonical_snapshot
from prompt_matrix.services.v1_orchestrator import verify_agreement, verify_artifact_integrity, verify_node_address_consistency

# === CONFIGURATION ===
TEST_PROJECT_ID = "fable-final-run-test"
TEST_DOCUMENT_PATH = "/path/to/test/document.pdf"  # ← Set this
TEST_DOCUMENT_BASENAME = "test-document.pdf"

# === STEP 0: Load test document ===
def load_test_document(path: str):
    """Load test document and return bundle, result, intake."""
    # Implement based on how documents are loaded in the system
    # This depends on the actual document loading mechanism
    pass

# === STEP 1: Create completion function (LLM grounding) ===
def create_completion_fn():
    """Create the LLM grounding completion function."""
    # Implement based on how LLM calls are made in the system
    # This should return a callable that takes prompt/messages and returns model response
    pass

# === STEP 2: Run the end-to-end proof test ===
def run_final_proof():
    """Execute the end-to-end proof test. Returns True if pass, False if fail."""
    
    # Load test document
    bundle, result, intake = load_test_document(TEST_DOCUMENT_PATH)
    
    # Create completion function
    completion_fn = create_completion_fn()
    
    # Build initial report
    initial_report = build_report_timed(
        project_id=TEST_PROJECT_ID,
        bundle=bundle,
        verification=None,
        filename=TEST_DOCUMENT_BASENAME,
        result=result,
        job_id="fable-final-run",
        intake=intake,
        completion=completion_fn,
        tree=None,
    )
    
    # Verify initial state
    assert initial_report["laya"]["status"] == "not_started", "Initial LAYA should be not_started"
    assert initial_report["verification"]["z3_status"] == "not_run", "Initial Z3 should be not_run"
    assert initial_report["replay"]["attempts"] == 0, "Initial replay attempts should be 0"
    assert initial_report["artifact_hash"] is not None, "Artifact hash should be set"
    assert initial_report["document_id"] is not None, "Document ID should be set"
    
    # Snapshot initial report
    initial_snapshot = canonical_snapshot(initial_report)
    initial_fields = {f["name"]: f for f in initial_report["fields"]}
    initial_node_ids = {f["name"]: f.get("node_id") for f in initial_report["fields"]}
    
    # Run LAYA/Red-Hat
    laya_result = run_adversarial_redhat(
        run_id=initial_report["report_id"],
        workspace_id=TEST_PROJECT_ID,
    )
    
    # Verify LAYA ran
    assert laya_result["laya"]["status"] == "completed", f"LAYA should be completed, got {laya_result['laya']['status']}"
    assert len(laya_result.get("findings", [])) > 0, "Red-Hat should find something"
    
    # Capture LAYA findings
    laya_findings = laya_result.get("findings", [])
    laya_affected_fields = set()
    for finding in laya_findings:
        field_name = extract_field_name_from_finding(finding)
        if field_name:
            laya_affected_fields.add(field_name)
    
    assert len(laya_affected_fields) > 0, "Red-Hat should affect at least one field"
    
    # Trigger rerun
    rerun_result = trigger_rerun(
        report=initial_report,
        laya_findings=laya_findings,
        project_id=TEST_PROJECT_ID,
        completion=completion_fn,
    )
    
    # Verify rerun happened
    assert rerun_result["replay"]["attempts"] >= 1, f"Rerun should have happened, got {rerun_result['replay']['attempts']} attempts"
    
    # Verify field output changed
    final_fields = {f["name"]: f for f in rerun_result["fields"]}
    changed_fields = []
    for field_name in laya_affected_fields:
        if field_name in initial_fields and field_name in final_fields:
            initial = initial_fields[field_name]
            final = final_fields[field_name]
            if (initial.get("value") != final.get("value") or
                initial.get("confidence") != final.get("confidence") or
                initial.get("field_state") != final.get("field_state")):
                changed_fields.append(field_name)
    
    assert len(changed_fields) > 0, \
        f"Rerun should have changed at least one field. " \
        f"Affected: {laya_affected_fields}, Changed: {changed_fields}"
    
    # Verify node pointers remained correct
    for field_name in initial_node_ids:
        if field_name in final_fields:
            initial_nid = initial_node_ids[field_name]
            final_nid = final_fields[field_name].get("node_id")
            assert initial_nid == final_nid, \
                f"Node pointer drifted for {field_name}: {initial_nid} -> {final_nid}"
    
    # Verify graph integrity
    assert rerun_result["graph_integrity"]["integrity_score"] >= 0.9, \
        f"Graph integrity score too low: {rerun_result['graph_integrity']['integrity_score']}"
    assert rerun_result["graph_integrity"]["orphan_fields"] == 0, \
        f"Orphan fields found: {rerun_result['graph_integrity']['orphan_fields']}"
    
    # Verify artifact hash
    assert verify_artifact_integrity(rerun_result), "Artifact hash mismatch"
    
    # Verify snapshot consistency
    final_snapshot = canonical_snapshot(rerun_result)
    assert final_snapshot != initial_snapshot, "Snapshot should have changed"
    
    # Verify JSON/UI/export/replay agreement
    ui_state = {"report_id": rerun_result["report_id"], "field_count": len(rerun_result["fields"])}
    export_state = {"report_id": rerun_result["report_id"]}
    replay_state = rerun_result
    
    agreement_issues = verify_agreement(
        json_state=rerun_result,
        ui_state=ui_state,
        export_state=export_state,
        replay_state=replay_state,
    )
    assert len(agreement_issues) == 0, f"Agreement issues: {agreement_issues}"
    
    # Verify export is NOT refused
    try:
        exported = safe_export(
            report=rerun_result,
            ui_state=ui_state,
            replay_state=replay_state,
            snapshot_at_creation=final_snapshot,
        )
        assert exported is not None, "Export should succeed"
    except Exception as e:
        if "ExportRefused" in str(type(e).__name__) or "refused" in str(e).lower():
            raise AssertionError(f"Export refused when it should have succeeded: {e}")
        raise
    
    # Verify export IS refused when integrity fails
    import copy
    tampered = copy.deepcopy(rerun_result)
    tampered["fields"][0]["value"] = "TAMPERED"
    tampered["artifact_hash"] = "wrong_hash"
    
    try:
        safe_export(tampered, ui_state, replay_state, snapshot_at_creation=final_snapshot)
        raise AssertionError("Export should have been refused for tampered report")
    except Exception as e:
        if "ExportRefused" not in str(type(e).__name__) and "refused" not in str(e).lower():
            raise AssertionError(f"Expected ExportRefused, got: {type(e).__name__}: {e}")
    
    # Verify grounding propagation
    for field_name in changed_fields:
        final_field = final_fields[field_name]
        if final_field.get("source") == "llm" or final_field.get("method") == "llm_fill":
            assert final_field.get("grounding_quote") is not None, \
                f"LLM field {field_name} should have grounding quote"
            assert final_field.get("grounding_span") is not None, \
                f"LLM field {field_name} should have grounding span"
            assert final_field.get("grounding_model") is not None, \
                f"LLM field {field_name} should have grounding model"
    
    return True

def extract_field_name_from_finding(finding):
    """Extract field name from a Red-Hat finding."""
    # Implement based on how findings are structured
    # This depends on the actual finding structure
    title = finding.get("title", "").lower()
    # Try to match title to field names
    for field_name in ["policy_number", "insured_name", "effective_date", "expiration_date"]:
        if field_name in title:
            return field_name
    return None

# === MAIN ===
if __name__ == "__main__":
    print("=" * 60)
    print("FABLE FINAL RUN: End-to-End Proof Test")
    print("=" * 60)
    print()
    print("Prerequisites:")
    print("  - All phases implemented")
    print("  - LLM grounding enabled")
    print("  - LAYA/Red-Hat wired")
    print("  - Z3 wired")
    print("  - Rerun loop wired")
    print("  - Artifact integrity gates implemented")
    print("  - Graph/node integrity contract implemented")
    print("  - Grounding propagation implemented")
    print("  - Safe export implemented")
    print("  - Export filename convention implemented")
    print("  - Test data available")
    print("  - Required functions available")
    print()
    print("Executing final run...")
    print()
    
    try:
        result = run_final_proof()
        if result:
            print("=" * 60)
            print("FINAL RUN: PASSED")
            print("=" * 60)
            print()
            print("Assure works as planned.")
            print("The result is real.")
            print()
            print("Evidence:")
            print("  - Red-Hat ran and produced findings")
            print("  - Rerun was triggered and executed")
            print("  - Field output changed after rerun")
            print("  - Node pointers remained correct (no drift)")
            print("  - Graph integrity passed")
            print("  - Artifact hash verified")
            print("  - Snapshot consistency verified")
            print("  - JSON/UI/export/replay all agreed")
            print("  - Export succeeded when integrity was good")
            print("  - Export was refused when integrity failed")
            print("  - Grounding propagation worked")
            print()
            sys.exit(0)
        else:
            print("=" * 60)
            print("FINAL RUN: FAILED")
            print("=" * 60)
            print()
            print("Assure does NOT work as planned.")
            print("Gaps and missing points identified.")
            print("Remediation required.")
            print()
            sys.exit(1)
    except AssertionError as e:
        print("=" * 60)
        print("FINAL RUN: FAILED")
        print("=" * 60)
        print()
        print(f"Assertion failed: {e}")
        print()
        print("Assure does NOT work as planned.")
        print("Specific gap identified.")
        print("Remediation required.")
        print()
        sys.exit(1)
    except Exception as e:
        print("=" * 60)
        print("FINAL RUN: ERROR")
        print("=" * 60)
        print()
        print(f"Error: {type(e).__name__}: {e}")
        print()
        print("Final run could not complete.")
        print("Check prerequisites and function availability.")
        print()
        sys.exit(2)
```

### Execution Command

```bash
cd /tmp/assure_repo
python test_final_run.py
```

### Expected Output

**If PASS:**
```
============================================================
FABLE FINAL RUN: End-to-End Proof Test
============================================================

Prerequisites:
  - All phases implemented
  ...

Executing final run...

============================================================
FINAL RUN: PASSED
============================================================

Assure works as planned.
The result is real.

Evidence:
  - Red-Hat ran and produced findings
  - Rerun was triggered and executed
  - Field output changed after rerun
  - Node pointers remained correct (no drift)
  - Graph integrity passed
  - Artifact hash verified
  - Snapshot consistency verified
  - JSON/UI/export/replay all agreed
  - Export succeeded when integrity was good
  - Export was refused when integrity failed
  - Grounding propagation worked
```

**If FAIL:**
```
============================================================
FABLE FINAL RUN: FAILED
============================================================

Assertion failed: Rerun should have happened, got 0 attempts

Assure does NOT work as planned.
Specific gap identified.
Remediation required.
```

**If ERROR:**
```
============================================================
FABLE FINAL RUN: ERROR
============================================================

Error: ImportError: No module named 'prompt_matrix.services.founder_redhat'

Final run could not complete.
Check prerequisites and function availability.
```

### Interpreting Results

- **PASS:** Assure works as planned. The result is real. Fable's work is complete.
- **FAIL (assertion):** A specific gap is identified. Remediation is done. The run is repeated until pass.
- **FAIL (exception):** A prerequisite is missing. Fix the prerequisite and repeat.
- **ERROR (import/module):** A function is not available. Implement the function first.

### After Final Run Passes

1. **Document the result:** Record that the final run passed, with timestamp and evidence.
2. **Archive the test data:** Keep the test document and results for future reference.
3. **Update the plan:** Mark all checklist items as complete.
4. **Report to stakeholder:** The final run passed. Assure works as planned.

### If Final Run Cannot Run

If the final run cannot run (missing prerequisites, missing functions, missing test data), document:
- What is missing
- Why it cannot run
- What needs to be done to enable it

The plan can still proceed with individual phases, but the final proof is deferred until the prerequisites are met.

---

## Part 2.11: Fable Proof Suite Handoff

**Goal**

Prove the pipeline actually changes output end-to-end, not just on paper.

**Canonical fixtures**

Use these three fixtures as the unambiguous proof suite:

1. `tests/golden/proof_suite_v1/auto_policy_low_quality.pdf`
2. `tests/golden/proof_suite_v1/medical_claim_z3.pdf`
3. `tests/golden/proof_suite_v1/mixed_bundle_page_coverage.pdf`

If those exact files do not exist yet, create them first and keep the names unchanged.

**Required assertions**

**Fixture 1: auto_policy_low_quality.pdf**

Assert that:
- At least one field has `value_quality` in `("garbage", "header_or_label", "address_fragment", "invalid_format")`
- At least one field has `provenance_confidence < 1.0`
- Not-found fields remain `field_state == "not_found"`
- Not-found fields have `routing_action == "field_not_found"`
- Not-found fields have `review_required is False`
- `grounding_quote` and `grounding_span` are present for any grounded extracted field
- `replay.attempts >= 1` if rerun is triggered
- At least one field changes between the initial and final report
- `graph_integrity["orphan_fields"] == 0`

**Fixture 2: medical_claim_z3.pdf**

Assert that:
- `verification["z3_status"] != "not_run"`
- `verification["z3_violation_count"]` is not None
- If a violation exists, it is reflected in the report conflicts / review routing
- No new field state vocabulary is introduced
- Document type remains consistent with the document family

**Fixture 3: mixed_bundle_page_coverage.pdf**

Assert that:
- Page-level coverage is present
- Mixed-bundle handling does not collapse all pages into one incorrect family
- `graph_integrity["orphan_fields"] == 0`
- `document_id` is stable and non-null
- Field node pointers remain valid after rerun/export
- Artifact snapshot / hash matches the canonical report snapshot
- JSON / UI / export / replay all agree on the final state

**Global assertions for the suite**

Across the full proof suite, assert that:

- Red-Hat / LAYA actually ran
- Rerun actually happened
- At least one field value changed because of rerun or grounding
- `grounding_quote` and `grounding_span` are propagated into the final report
- `provenance_confidence` is not always 1.0
- `verification_confidence` is not blindly 0.85 on not-found fields
- `field_state` uses only the existing vocabulary
- `routing_action` uses only the existing vocabulary
- `graph_integrity` has no orphan fields
- Export is refused if artifact hash / snapshot mismatch exists
- The final UI, JSON, export, and replay artifacts all match

**Hard stop conditions**

Fail the run immediately if any of these happen:

- A new state value is introduced outside the existing constants
- `verification["z3_status"] == "not_run"` for the Z3 fixture
- `replay.attempts == 0` when rerun should have been triggered
- `graph_integrity["orphan_fields"] > 0`
- `document_id` is null or unstable
- Artifact hash / snapshot mismatch is ignored instead of refusing export
- UI / JSON / export / replay disagree on the final field state

**Success criterion**

The suite only passes if all three fixtures pass and the final output visibly proves:

- Red-Hat ran
- Rerun changed something
- Grounding is present
- Graph integrity is clean
- Artifacts agree

**Implementation note**

Do not treat this as optional validation.

This suite is the completion gate for the work.

---

## Part 3: Table-Aware Extraction (Phase 3)




---



Tables are already extracted by parsers (Textract, Docling) and stored in the bundle/JDF. But they're flattened to text and the structure is lost. This phase preserves structure and adds table-aware extraction.

### 3.1 Preserve table structure for extraction

**File:** `prompt_matrix/services/field_extractor.py`, the function that processes JDF nodes (around line 1175-1185)

**Current:** Table nodes are flattened to text:
```python
if node.get("type") == "table":
    cells = [str(c) for row in node.get("rows") or [] for c in row]
    text = "\n".join([" | ".join(str(h) for h in node.get("headers") or [])] + 
                     [" | ".join(str(c) for c in row) for row in node.get("rows") or []])
```

**Fix:**
```python
# Before extraction, collect table nodes separately:
tables = []
for node in jdf_nodes:
    if node.get("type") == "table":
        tables.append({
            "node_id": node.get("id"),
            "node_type": "table",
            "headers": node.get("headers") or [],
            "rows": node.get("rows") or [],
            "caption": node.get("caption"),
            "cell_count": sum(len(row) for row in (node.get("rows") or [])),
        })

# Pass tables to extraction:
seg_fields, seg_rules = extract_segment_fields(
    ...,
    tables=tables,  # NEW parameter
    ...
)
```

**Why:** Tables are currently flattened to text, losing the 2D structure. By preserving the structure, extraction can look at table headers, rows, and cells directly.

---

### 3.2 Add table-aware extraction for key fields

**File:** `prompt_matrix/services/field_extractor.py`, `extract_segment_fields`

**Current:** Extraction only does label-anchored matching on flattened text.

**Fix:**
```python
def extract_segment_fields(document_type, texts, layout, parser_name, parse_confidence, 
                           ocr_confidence, page_quality, visual_pages, verification, notes, 
                           completion, project_id, schema_mismatch, tables=None):
    """Extract fields from segment. tables is a list of table dicts with headers, rows, etc."""
    
    fields = []
    
    # Existing label-anchored extraction
    for spec in relevant_specs:
        label_match = find_label_match(texts, spec)
        if label_match:
            fields.append(label_match)
    
    # NEW: Table-aware extraction
    if tables:
        for spec in relevant_specs:
            # Skip if already found by label pass with high confidence
            if any(f.get("name") == spec.name and f.get("confidence", 0) > 0.8 for f in fields):
                continue
            
            # Check if spec could be in a table
            table_match = find_in_tables(tables, spec)
            if table_match:
                fields.append(table_match)
    
    return fields, rules

def find_in_tables(tables, spec):
    """Find a field value in tables by matching headers to spec label."""
    for table in tables:
        headers = [str(h).lower() for h in table.get("headers") or []]
        for header in headers:
            # Check if header matches spec label (fuzzy match)
            if header_match(header, spec.label) or header_match(header, spec.anchors):
                # Extract values from this column
                col_index = headers.index(header)
                values = []
                for row in table.get("rows") or []:
                    if col_index < len(row):
                        values.append(str(row[col_index]))
                
                if values:
                    # Return the most likely value (e.g., first non-empty, or most common)
                    value = best_value(values)
                    return {
                        "name": spec.name,
                        "value": value,
                        "confidence": 0.7,  # table extraction confidence
                        "source": "table",
                        "table_node_id": table["node_id"],
                        "table_header": header,
                        "table_column_index": col_index,
                        "method": "table_extraction",
                    }
    return None
```

**Why:** Tables often contain key values (premium, deductible, coverage amounts) in structured columns. By matching table headers to field labels, we can extract these values even when the label doesn't appear as text before the value.

**Test:** Run on a document with a table containing premium/deductible/coverage. Verify these values are extracted from the table even if the label doesn't appear as text.

---

### 3.3 Add table quality assessment

**File:** `prompt_matrix/services/quality_probe.py` or new

**Fix:**
```python
def assess_table_quality(table):
    """Assess the quality of a table extraction."""
    issues = []
    score = 1.0
    
    # Check headers
    headers = table.get("headers") or []
    if not headers:
        issues.append("no_headers")
        score -= 0.3
    
    # Check for merged cells (rows with different lengths)
    rows = table.get("rows") or []
    row_lengths = [len(row) for row in rows]
    if row_lengths and len(set(row_lengths)) > 1:
        issues.append("irregular_rows")
        score -= 0.2
    
    # Check for empty cells
    empty_cells = sum(1 for row in rows for cell in row if not cell or not str(cell).strip())
    total_cells = sum(len(row) for row in rows)
    if total_cells > 0 and empty_cells / total_cells > 0.3:
        issues.append("many_empty_cells")
        score -= 0.2
    
    return {
        "score": max(0, score),
        "issues": issues,
        "header_count": len(headers),
        "row_count": len(rows),
        "cell_count": total_cells,
    }
```

**Why:** Not all table extractions are perfect. Assessing quality helps flag problematic tables for review.

---

## Part 4: Schema Extensibility (Phase 4)

Currently, the taxonomy is static code (`_f()` calls in `field_extractor.py`). Adding new document types or fields requires code changes. This phase adds a schema registry for runtime extensibility.

### 4.1 Add schema registry

**File:** New `prompt_matrix/services/schema_registry.py` or extend `field_extractor.py`

**Fix:**
```python
# schema_registry.py
from typing import Any
from prompt_matrix.services.field_extractor import FieldSpec

# Default schemas (from static taxonomy)
DEFAULT_SCHEMAS = {
    "auto_policy": [...],  # auto_policy field specs
    "homeowners_policy": [...],  # homeowners field specs
    "title_policy": [...],  # title policy field specs
    "health_insurance": [...],  # health insurance field specs
    # ... etc
}

# Runtime schemas (can be updated without code changes)
RUNTIME_SCHEMAS: dict[str, list[FieldSpec]] = {}

def get_schema(document_type: str) -> list[FieldSpec] | None:
    """Get schema for a document type. Checks runtime first, then defaults."""
    # Check runtime schemas first
    if document_type in RUNTIME_SCHEMAS:
        return RUNTIME_SCHEMAS[document_type]
    
    # Check default schemas
    if document_type in DEFAULT_SCHEMAS:
        return DEFAULT_SCHEMAS[document_type]
    
    # Check family-based fallback
    family = document_type.split("_")[0] if "_" in document_type else document_type
    if family in DEFAULT_SCHEMAS:
        return DEFAULT_SCHEMAS[family]
    
    return None

def register_schema(document_type: str, fields: list[FieldSpec]) -> None:
    """Register a schema at runtime. Can be called from config, API, or database."""
    RUNTIME_SCHEMAS[document_type] = fields

def list_schemas() -> list[str]:
    """List all registered document types."""
    return list({**DEFAULT_SCHEMAS, **RUNTIME_SCHEMAS}.keys())
```

**Why:** New document types can be added by registering a schema at runtime, without code changes. This enables extensibility.

---

### 4.2 Add dynamic field discovery for unknown document types

**File:** `prompt_matrix/services/field_extractor.py`, `extract_segment_fields`

**Current:** Unknown document type → `<family>_unknown` with no fields.

**Fix:**
```python
def extract_segment_fields(..., document_type, ...):
    schema = get_schema(document_type)
    
    if schema:
        # Normal extraction with known schema
        fields = extract_with_schema(texts, schema, ...)
    else:
        # Unknown document type — try dynamic discovery
        fields = extract_with_schema(texts, DEFAULT_SCHEMAS.get("generic"), ...)
        
        # Additionally, try to discover fields from text
        if completion:
            discovered = discover_fields_from_text(texts, completion, document_type)
            fields.extend(discovered)
        else:
            # Fallback: simple heuristics
            discovered = discover_fields_heuristic(texts)
            fields.extend(discovered)
    
    return fields, rules

def discover_fields_from_text(texts, completion, document_type):
    """Use LLM to discover fields in unknown document type."""
    prompt = f"""
    You are analyzing an insurance document of type "{document_type}".
    The text content is:
    {texts}
    
    Identify all key-value pairs that look like insurance fields.
    Return a JSON array of objects with: name, value, confidence, source.
    """
    result = completion(prompt)
    # Parse result and return as fields
    return parse_discovered_fields(result)

def discover_fields_heuristic(texts):
    """Simple heuristic field discovery (fallback when LLM not available)."""
    fields = []
    # Look for patterns like "Label: Value" or "Label - Value"
    for line in texts:
        # Simple pattern matching
        ...
    return fields
```

**Why:** Unknown document types get some extraction rather than nothing. LLM can help discover fields; heuristic fallback works without LLM.

---

### 4.3 Handle extra intake parameters

**File:** `prompt_matrix/services/v1_orchestrator.py`, `build_report_timed`

**Current:** Extra intake parameters beyond `visual_pages`, `laya`, `material_type`, etc. are ignored.

**Fix:**
```python
# In build_report_timed, when building the report:

# Capture extra intake parameters for audit trail
known_intake_keys = {"visual_pages", "laya", "material_type", "modality", "source_kind", 
                     "claim_context", "adjuster_notes", "upload_metadata"}
extra_intake = {}
for key, value in (intake or {}).items():
    if key not in known_intake_keys:
        extra_intake[key] = value

report["intake_extra"] = extra_intake if extra_intake else None
```

**Why:** Extra intake parameters are captured for audit trail. New parameters (e.g., `claim_context`, `adjuster_notes`) can be added to intake and will be recorded even if not yet used in extraction.

---

### 4.4 Add extensibility for new document types

**File:** Schema registry + classification

**Fix:**
- Adding a new document type = adding a schema to the registry (list of field specs)
- The classification already identifies document types — new types just need schemas
- No code changes to the extraction pipeline needed for new document types (just new schemas)

**For new document types via config:**
```python
# config/schemas/auto_umbrella_policy.py
from prompt_matrix.services.schema_registry import register_schema
from prompt_matrix.services.field_extractor import _f

register_schema("auto_umbrella_policy", [
    _f("umbrella_limit", "Umbrella limit", "money", (r"umbrella\s*(?:limit|amount)")),
    _f("underlying_policies", "Underlying policies", "text", (r"underlying\s*(?:policies|policy)")),
    # ... etc
])
```

**Why:** New document types can be added by dropping a config file with the schema, without touching the core extraction code.

---

## Part 5: Picture/Vision Model Handling (Phase 5)

This is the biggest gap — nothing exists for vision-based picture analysis. This phase adds it from scratch.

### 5.1 Add vision model integration

**File:** New `prompt_matrix/services/vision_service.py`

**Fix:**
```python
# vision_service.py
from typing import Any

# Vision model configuration
VISION_MODEL = "gpt-4v"  # or "claude-vision", "gemini-vision", etc.
VISION_API_KEY = ...  # from config/secrets

def analyze_photo(image_bytes: bytes, analysis_type: str, extra_context: str = "") -> dict[str, Any]:
    """
    Analyze a photo using a vision model.
    
    analysis_type: "accident_scene", "property_exterior", "property_interior", 
                   "damage_closeup", "roof", "water_damage", "fire_damage", etc.
    extra_context: additional context for the analysis (e.g., "claim number XYZ, policy type auto")
    
    Returns structured analysis result.
    """
    # Build prompt based on analysis type
    prompt = _build_analysis_prompt(analysis_type, extra_context)
    
    # Call vision model
    response = _call_vision_model(image_bytes, prompt)
    
    # Parse response into structured data
    return _parse_vision_response(response, analysis_type)

def _build_analysis_prompt(analysis_type: str, extra_context: str) -> str:
    """Build the prompt for vision model based on analysis type."""
    prompts = {
        "accident_scene": """
            Analyze this accident scene photo. Identify and describe:
            1. Vehicles involved (count, positions, makes/models if visible)
            2. Damage to each vehicle (location, severity, type)
            3. Road conditions (wet, dry, icy, surface type)
            4. Weather conditions (clear, rainy, foggy, etc.)
            5. Traffic controls visible (lights, signs, marks)
            6. Scene context (urban, rural, highway, intersection, etc.)
            7. Any other relevant details for an insurance claim
            
            Return structured JSON with these categories.
        """,
        "property_exterior": """
            Analyze this property exterior photo. Identify and describe:
            1. Property type (house, apartment, commercial, etc.)
            2. Roof condition (intact, damaged, missing shingles, etc.)
            3. Siding/brick condition
            4. Windows and doors condition
            5. Foundation visible
            6. Surrounding area (landscape, other structures)
            7. Any visible damage or issues
            
            Return structured JSON with these categories.
        """,
        "damage_closeup": """
            Analyze this closeup damage photo. Identify and describe:
            1. Type of damage (crack, hole, water stain, burn, break, etc.)
            2. Location on surface (wall, ceiling, floor, object, etc.)
            3. Size/extent of damage (estimate relative size)
            4. Severity (minor, moderate, severe)
            5. Possible cause (impact, water, fire, wear, etc.)
            6. Any other relevant details
            
            Return structured JSON with these categories.
        """,
    }
    base = prompts.get(analysis_type, prompts["damage_closeup"])
    if extra_context:
        base += f"\n\nAdditional context: {extra_context}"
    return base

def _call_vision_model(image_bytes: bytes, prompt: str) -> str:
    """Call the vision model with image and prompt. Return raw response."""
    # Implementation depends on which vision model is used
    # Example for GPT-4V:
    # import base64
    # from openai import OpenAI
    # client = OpenAI(api_key=VISION_API_KEY)
    # image_b64 = base64.b64encode(image_bytes).decode()
    # response = client.chat.completions.create(
    #     model="gpt-4v",
    #     messages=[{"role": "user", "content": [
    #         {"type": "text", "text": prompt},
    #         {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
    #     ]}],
    # )
    # return response.choices[0].message.content
    raise NotImplementedError("Vision model call not implemented")

def _parse_vision_response(response: str, analysis_type: str) -> dict[str, Any]:
    """Parse vision model response into structured data."""
    # Try to parse as JSON first
    try:
        import json
        return json.loads(response)
    except:
        pass
    
    # Fallback: return as text summary
    return {
        "raw_response": response,
        "analysis_type": analysis_type,
        "parsed": False,
    }
```

**Why:** This is the foundational piece for picture handling. Without it, nothing else in Phase 5 works.

**Implementation note:** The `_call_vision_model` function needs to be implemented based on which vision model is available. The structure above shows the interface.

---

### 5.2 Add picture-to-structured-data extraction

**File:** New or in extraction pipeline

**Fix:**
```python
def extract_from_vision_analysis(vision_result: dict, document_type: str) -> list[dict]:
    """Convert vision analysis result into extractable fields."""
    fields = []
    
    # Map vision results to existing field types where possible
    if document_type == "auto_policy" and "accident_scene" in vision_result:
        scene = vision_result["accident_scene"]
        
        # Extract vehicle count
        if "vehicles" in scene:
            fields.append({
                "name": "vehicle_count",
                "value": str(scene["vehicles"].get("count", 0)),
                "confidence": 0.8,
                "source": "vision",
                "analysis_type": "accident_scene",
                "method": "vision_extraction",
            })
        
        # Extract damage description
        if "damage" in scene:
            damage_desc = "; ".join(
                f"Vehicle {v.get('position', '?')}: {v.get('damage_description', '?')}"
                for v in scene["damage"]
            )
            fields.append({
                "name": "loss_description",
                "value": damage_desc,
                "confidence": 0.7,
                "source": "vision",
                "analysis_type": "accident_scene",
                "method": "vision_extraction",
            })
    
    # Add more mappings for other document types and analysis types
    
    return fields
```

**Why:** Converts vision analysis into the same field format used by the extraction pipeline. These fields can then be merged with text-extracted fields.

---

### 5.3 Wire picture analysis into the pipeline

**File:** `prompt_matrix/services/v1_orchestrator.py`, `build_report_timed` or a new pipeline stage

**Fix:**
```python
# In build_report_timed, after intake but before extraction:

# Check if visual pages contain images that could be analyzed
visual_pages = [p.get("visual") for p in pages] if pages else []
image_pages = []  # pages that contain images suitable for vision analysis

for i, page in enumerate(visual_pages):
    if page and page.get("has_image"):  # or some indicator
        image_pages.append(i)

# If there are images and vision is enabled, analyze them
vision_fields = []
if image_pages and vision_enabled:
    for page_index in image_pages:
        # Get the image for this page
        image_bytes = get_page_image(bundle, page_index)  # implement this
        
        # Determine analysis type based on document type and page content
        analysis_type = determine_analysis_type(document_type, page_index, texts)
        
        # Analyze the image
        try:
            vision_result = analyze_photo(image_bytes, analysis_type, 
                                         extra_context=f"Document type: {document_type}")
            
            # Convert to fields
            page_vision_fields = extract_from_vision_analysis(vision_result, document_type)
            for f in page_vision_fields:
                f["page_index"] = page_index
            vision_fields.extend(page_vision_fields)
        except Exception as e:
            log.warning("Vision analysis failed for page %s: %s", page_index, e)
    
    # Merge vision fields with text-extracted fields
    # Vision fields have lower priority than text-extracted fields with high confidence
    for vf in vision_fields:
        # Check if a text-extracted field with same name already exists with higher confidence
        existing = next((f for f in fields if f.get("name") == vf.get("name") and 
                         f.get("confidence", 0) > vf.get("confidence", 0)), None)
        if not existing:
            fields.append(vf)
```

**Why:** Automatically analyzes pictures in the document and adds the results as extractable fields. This is the integration point between vision analysis and the extraction pipeline.

---

### 5.4 Add picture-specific fields to taxonomy

**File:** `prompt_matrix/services/field_extractor.py`, taxonomy

**Fix:**
```python
# Add new field specs for picture-based extraction
_f("scene_summary", "Scene summary", "text", (r"scene\s+summary", r"accident\s+scene")),
_f("vehicle_count_visual", "Vehicle count (visual)", "number", (r"vehicle\s*count")),
_f("damage_description_visual", "Damage description (visual)", "text", (r"damage\s+description")),
_f("property_condition_visual", "Property condition (visual)", "text", (r"property\s+condition")),
_f("roof_condition_visual", "Roof condition (visual)", "text", (r"roof\s+condition")),
_f("water_damage_visual", "Water damage (visual)", "text", (r"water\s+damage")),
_f("fire_damage_visual", "Fire damage (visual)", "text", (r"fire\s+damage")),
_f("vehicles_involved_visual", "Vehicles involved (visual)", "text", (r"vehicles\s+involved")),
```

**Why:** These fields are specifically for values sourced from vision analysis. They're distinguished from text-extracted fields by name and source.

---

### 5.5 Add picture quality assessment

**File:** `prompt_matrix/services/quality_probe.py` or new

**Fix:**
```python
def assess_picture_quality(image_bytes: bytes) -> dict:
    """Assess the quality of a picture for insurance analysis."""
    issues = []
    score = 1.0
    
    # Check basic image properties
    try:
        from PIL import Image
        img = Image.open(image_bytes)
        width, height = img.size
        
        # Resolution check
        if width < 800 or height < 600:
            issues.append("low_resolution")
            score -= 0.2
        
        # Aspect ratio check (very narrow or square might be suboptimal)
        aspect = width / height
        if aspect < 0.5 or aspect > 2.5:
            issues.append("unusual_aspect_ratio")
            score -= 0.1
        
        # Check for blur (simple edge detection)
        # This is a placeholder — real implementation would use more sophisticated methods
        # blur_score = compute_blur(img)
        # if blur_score > threshold:
        #     issues.append("blurry")
        #     score -= 0.3
        
    except Exception as e:
        issues.append(f"image_analysis_failed: {e}")
        score = 0.0
    
    return {
        "score": max(0, score),
        "issues": issues,
        "width": width if 'width' in dir() else None,
        "height": height if 'height' in dir() else None,
    }
```

**Why:** Not all pictures are suitable for analysis. Low-resolution, blurry, or poorly-angled photos should be flagged for human review or re-capture.

---

## Part 6: UI Connections (Phase 6)

This phase covers how the output gets displayed to users and how they interact with it. This is the "enterprise look and execution" part — making sure the output is usable and the UI reflects the execution.

### 6.1 UI: Display field states clearly

**Current:** Fields in the UI show name, value, confidence. But the state (not_found, found_suspect, unverified, found) may not be clearly displayed.

**Fix:**
- Add visual indicators for field states:
  - `not_found`: grayed out, "Not found" badge
  - `found_suspect`: yellow warning, "Suspect" badge
  - `unverified`: blue info, "Unverified" badge
  - `found`: green check, "Found" badge (or no badge if auto-accepted)
  - `found` with `review_required`: orange, "Review" badge
- Add confidence bar/indicator (e.g., 0-100% bar)
- Add provenance indicator (e.g., "from label", "from table", "from vision", "from LLM")

**UI component sketch:**
```
Field: policy_number
Value: 123456789
State: [Found] [Confidence: 95%] [Source: Label]
       [Verification: 0.85] [Provenance: 1.0]
```

For suspect fields:
```
Field: insured_name
Value: 123 Main Street (HEADER TEXT)
State: [Suspect] [Confidence: 40%] [Source: Label]
       [Value quality: header_or_label] [Provenance: 0.3]
```

For not-found fields:
```
Field: vin
Value: —
State: [Not Found] [Review: Not Required]
       [Reason: No value found under label]
```

---

### 6.2 UI: Display review summary

**Current:** Review summary may show total fields and review count, but not clearly broken down by state.

**Fix:**
- Show review summary as:
  - Total fields: X
  - Found & auto-accepted: Y
  - Found & needs review: Z
  - Found & suspect: W
  - Not found: V
- Add action buttons:
  - "Review all flagged" → jumps to first flagged field
  - "Export report" → exports current state
  - "Accept all auto-accepted" → bulk accept

---

### 6.3 UI: Display execution status

**Current:** The UI may not show whether LAYA, Z3, rerun, or LLM grounding ran.

**Fix:**
- Add execution status panel:
  - LAYA: [Completed] [Findings: X] [Status: pass/violation/pending]
  - Z3 Verification: [Completed] [Violations: X] [Status: pass/violation/not_run]
  - Rerun: [Completed] [Attempts: X] [Improved: yes/no] [Low-confidence remaining: X]
  - LLM Grounding: [Completed] [Fields grounded: X] [Quotes: X]
- Add timestamps for each execution step

---

### 6.4 UI: Display grounding evidence

**Current:** If LLM grounding runs, grounding_quote and grounding_span should be in the output. The UI should display them.

**Fix:**
- For each field with grounding:
  - Show the grounding quote (the text from the document that supports the extracted value)
  - Show the grounding span (the location in the document — page, position)
  - Show the LLM that provided the grounding (if available)
- Make grounding clickable — clicking shows the quote in context (highlights the text in the document viewer)

---

### 6.5 UI: Handle picture analysis results

**Fix:**
- For documents with picture analysis:
  - Show the analyzed image with annotations (e.g., bounding boxes for vehicles, damage highlights)
  - Show the vision analysis result in a structured panel:
    - Accident scene: vehicles, damage, conditions
    - Property: condition, damage, areas
  - Allow users to click on an annotation to see the detail
  - Allow users to flag a picture as low quality or re-upload
- Picture quality indicator:
  - Green check: good quality
  - Yellow warning: acceptable but not ideal
  - Red X: poor quality, needs re-capture

---

### 6.6 UI: Handle table results

**Fix:**
- For documents with tables:
  - Show the table in a structured grid (not just flattened text)
  - Highlight which cells were extracted as field values
  - Show table quality assessment (headers OK, irregular rows, etc.)
- Clicking a table cell shows which field it was extracted as (if any)

---

### 6.7 UI: Handle rerun results

**Fix:**
- If rerun happened:
  - Show rerun history: attempt 1, attempt 2, etc.
  - For each attempt, show what approach was used and what improved
  - Show final low-confidence fields that couldn't be improved
- Allow users to manually trigger rerun on specific fields

---

### 6.8 UI: Enterprise polish

**Fix:**
- Consistent styling across all panels
- Professional color scheme (not just default blue)
- Clear typography and spacing
- Loading states for long-running operations (LAYA, Z3, vision analysis, rerun)
- Error states with clear messages and retry options
- Empty states (what to show when no data)
- Responsive layout (works on different screen sizes)
- Export functionality (export report as PDF/JSON/structured data)
- Audit trail display (timestamped log of all actions and execution steps)

---

## Part 7: Verification and Testing (Phase 7)

### 7.1 Verify output reflects execution

After all phases are implemented, verify the output shows:

**Execution status:**
- `laya.status`: `"completed"` with findings (not `"not_started"`)
- `verification.z3_status`: `"pass"` or `"violation"` with counts (not `"not_run"`)
- `replay.attempts`: > 0 if rerun happened, with actual history
- `llm_grounding`: completed with quotes/spans

**Confidence values:**
- `provenance_confidence`: reflects value quality (not always 1.0)
- `verification_confidence`: reflects actual verification (not 0.85 default for all)
- `confidence`: reflects actual extraction confidence

**Field states:**
- Found-but-suspect fields: `field_state: "unverified"`, `routing_action: "manual_review"`, `evidence_state: "found_suspect"`

- Found fields: appropriate state based on quality and verification

**Review summary:**
- Correctly separates not-found from review items
- Counts match actual field states

**Tables:**
- Table structure preserved in output (not just flattened text)
- Table-extracted fields have source: "table"

**Pictures:**
- Vision analysis results in output (if pictures present and vision enabled)
- Picture quality assessment in output

---

### 7.2 Test with the same documents that produced bad output

**Documents:** The JSON files from prior work (parsure-pr-56a8f5ce4f344134.json, etc.)

**Check:**
- Run the pipeline on the same documents
- Verify the output shows:
  - Correct `field_state` for not-found fields
  - Correct `provenance_confidence` for garbage values (low, not 1.0)
  - LAYA findings populated
  - Z3 status populated
  - Rerun history populated if rerun happened
  - Grounding quotes present (if LLM grounding runs)
  - Review summary correctly separated

---

### 7.3 Test with new document types

**Test:**
- Add a new document type via schema registry (e.g., "auto_umbrella_policy")
- Verify the new document type is classified correctly
- Verify the new schema's fields are extracted
- Verify no code changes were needed (just schema registration)

---

### 7.4 Test with pictures

**Test:**
- Upload a document with accident scene photos
- Verify vision analysis runs
- Verify picture-based fields are extracted
- Verify picture quality assessment is in the output
- Verify UI displays picture analysis results

---

### 7.5 Test with tables

**Test:**
- Upload a document with tables containing premium/deductible/coverage
- Verify table structure is preserved
- Verify table-aware extraction finds values in table columns
- Verify UI displays table in structured grid

---

### 7.6 Test edge cases

**Test:**
- Document with no text (image-only PDF) — verify handling
- Document with mixed content (text + tables + pictures) — verify all extraction paths work
- Document with very low quality — verify quality flags and review routing
- Document with unknown type — verify dynamic field discovery or graceful handling
- Document with extra intake parameters — verify they're captured in output


---

## Part 8: Artifact Integrity Gates

The pipeline must enforce truth, or refuse to export. Not just "add more capabilities."

### 8.1 Canonical Snapshot

**File:** `prompt_matrix/services/v1_orchestrator.py`, in `build_report_timed` (before returning report)

**Requirement:** Before returning the report, compute a canonical snapshot hash of the entire report.

**Fix:**
```python
import hashlib
import json

def canonical_snapshot(report: dict) -> str:
    """Compute a canonical hash of the report for integrity checking."""
    # Canonicalize: sort keys, convert to JSON with consistent formatting
    canonical = json.dumps(report, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]

# In build_report_timed, before return:
report["artifact_hash"] = canonical_snapshot(report)
report["artifact_version"] = "1.0"

return report
```

**Why:** Every report gets a canonical hash. This hash can be used to verify that the report hasn't been corrupted or modified incompletely.

**Test:** Generate two identical reports. Verify they have the same artifact_hash.

---

### 8.2 Artifact Hash Gate

**File:** Export functions, API endpoints that return the report

**Requirement:** Before exporting/serializing the report, verify the artifact hash matches the canonical snapshot.

**Fix:**
```python
def verify_artifact_integrity(report: dict) -> bool:
    """Verify that the report's artifact_hash matches its canonical snapshot."""
    stored_hash = report.get("artifact_hash")
    if not stored_hash:
        return False  # No hash = not verified
    
    computed_hash = canonical_snapshot(report)
    return stored_hash == computed_hash

def export_report(report: dict) -> dict:
    """Export the report. Refuse if integrity check fails."""
    if not verify_artifact_integrity(report):
        raise IntegrityError("Report artifact hash mismatch — export refused")
    return {
        **report,
        "export_timestamp": _now(),
        "exported_by": "system",
    }
```

**Why:** Prevents exporting corrupted or partially-modified reports. If the hash doesn't match, the export is refused.

**Test:** Modify a field in the report after computing the hash. Verify export refuses.

---

### 8.3 JSON/UI/Export/Replay Agreement Check

**File:** Export functions, API endpoints

**Requirement:** The JSON, UI, export, and replay must all agree on the same artifact.

**Fix:**
```python
def verify_agreement(report: dict, ui_state: dict, export_state: dict, replay_state: dict) -> list[str]:
    """Check that JSON, UI, export, and replay all agree."""
    issues = []
    
    # Check JSON report_id matches UI and export
    if report.get("report_id") != ui_state.get("report_id"):
        issues.append("report_id mismatch: JSON vs UI")
    if report.get("report_id") != export_state.get("report_id"):
        issues.append("report_id mismatch: JSON vs export")
    
    # Check field counts match
    json_field_count = len(report.get("fields") or [])
    ui_field_count = ui_state.get("field_count") or 0
    if json_field_count != ui_field_count:
        issues.append(f"field count mismatch: JSON={json_field_count} vs UI={ui_field_count}")
    
    # Check replay state matches
    json_replay = report.get("replay") or {}
    replay_state_replay = replay_state.get("replay") or {}
    if json_replay.get("attempts") != replay_state_replay.get("attempts"):
        issues.append(f"replay attempts mismatch: JSON={json_replay.get('attempts')} vs replay={replay_state_replay.get('attempts')}")
    
    # Check LAYA status matches
    json_laya = report.get("laya") or {}
    ui_laya = ui_state.get("laya") or {}
    if json_laya.get("status") != ui_laya.get("status"):
        issues.append(f"laya status mismatch: JSON={json_laya.get('status')} vs UI={ui_laya.get('status')}")
    
    return issues
```

**Why:** Prevents the UI from showing different data than the JSON, or the export from being different from what was computed.

**Test:** Generate a report, simulate UI state, export state, and replay state. Verify no mismatches.

---

### 8.4 Export Refusal on Mismatch

**Requirement:** If any integrity check fails, the export is refused and an error is returned.

**Fix:**
```python
class IntegrityError(Exception):
    """Raised when report integrity check fails."""
    pass

def safe_export(report: dict, ui_state: dict, export_state: dict, replay_state: dict) -> dict:
    """Export after all integrity checks pass."""
    
    # Check 1: Artifact hash
    if not verify_artifact_integrity(report):
        raise IntegrityError("Artifact hash mismatch")
    
    # Check 2: Agreement between JSON/UI/export/replay
    issues = verify_agreement(report, ui_state, export_state, replay_state)
    if issues:
        raise IntegrityError(f"Agreement violations: {'; '.join(issues)}")
    
    # All checks passed — safe to export
    return export_report(report)
```

**Why:** The pipeline enforces truth. If anything is inconsistent, it refuses to export rather than exporting bad data.

### 8.5 Export Filename Convention

**Requirement:** The export filename must include what is parsed and key data points.

**Current:** Exports are probably named `report-{uuid}.json` or similar generic names.

**Fix:**
```python
def build_export_filename(report: dict) -> str:
    """Build a descriptive export filename based on what is parsed."""
    # Extract key information from the report
    document_type = report.get("classification", {}).get("document_type", "unknown")
    document_id = report.get("document_id", "unknown")
    policy_number = None
    insured_name = None
    effective_date = None
    
    # Find key data points from fields
    for field in report.get("fields") or []:
        name = field.get("name", "")
        value = field.get("value")
        if name == "policy_number" and value:
            policy_number = str(value)[:20]  # truncate long values
        elif name == "insured_name" and value:
            insured_name = str(value)[:30]
        elif name == "effective_date" and value:
            effective_date = str(value)[:10]
    
    # Build filename components
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    type_prefix = document_type.replace("_", "-") if document_type else "unknown"
    
    # Build filename
    parts = [type_prefix, document_id]
    if policy_number:
        parts.append(f"policy-{policy_number}")
    if insured_name:
        parts.append(f"insured-{insured_name.replace(' ', '_')}")
    if effective_date:
        parts.append(f"eff-{effective_date}")
    
    # Join and add timestamp and extension
    name_body = "_".join(parts)
    filename = f"{name_body}_{timestamp}.json"
    
    # Sanitize filename (remove problematic characters)
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    
    return filename

# In export_report, use the filename:
def export_report(report: dict, output_dir: str = ".") -> str:
    """Export the report to a file with a descriptive name. Refuse if integrity check fails."""
    if not verify_artifact_integrity(report):
        raise IntegrityError("Report artifact hash mismatch — export refused")
    
    # Verify agreement
    issues = verify_agreement(report, ..., ..., ...)
    if issues:
        raise IntegrityError(f"Agreement violations: {'; '.join(issues)}")
    
    # Build filename
    filename = build_export_filename(report)
    filepath = os.path.join(output_dir, filename)
    
    # Write the export
    export_data = {
        **report,
        "export_timestamp": _now(),
        "exported_by": "system",
        "export_filename": filename,
    }
    with open(filepath, "w") as f:
        json.dump(export_data, f, indent=2, default=str, ensure_ascii=False)
    
    return filepath

# Example filenames:
# auto-policy_doc-abc123_policy-ABC-123456789_insured-John_Smith_eff-2026-01-01_20260927_150000.json
# homeowners-policy_doc-xyz789_policy-HOME-987654_insured-Jane_Doe_20260927_160000.json
# title-policy_doc-def456_20260927_170000.json  (no policy number found)
```

**Why:** The export filename tells you what was parsed without opening the file. You can see:
- Document type (auto-policy, homeowners-policy, title-policy)
- Document ID
- Key data points (policy number, insured name, effective date)
- Timestamp

This makes exports self-describing and easy to identify.

**Test:** Export a report. Verify the filename includes document type, document ID, and available key data points.

---

## Part 9: Graph Integrity / Negative Evidence / Orphan Handling

---



### 9.1 Negative Evidence Nodes for Missing Fields

**Requirement:** For every field that is NOT found, create a negative evidence node in the graph.

**Fix:**
```python
def create_negative_evidence_node(field: dict, document_id: str, node_id_policy: dict) -> dict:
    """Create a negative evidence node for a missing field."""
    return {
        "id": node_id_policy.generate("ne", document_id, field["name"]),
        "type": "negative_evidence",
        "field_name": field["name"],
        "status": "not_found",
        "reason": field.get("reason") or "No value found under label",
        "confidence": 0.0,
        "segment": field.get("segment"),
        "document_id": document_id,
        "created_at": _now(),
    }

# In build_report_timed, after building fields:
negative_evidence_nodes = []
for field in fields:
    if field.get("field_state") == "not_found":
        neg_node = create_negative_evidence_node(field, report["document_id"], fx.NODE_ID_POLICY)
        negative_evidence_nodes.append(neg_node)

report["negative_evidence"] = negative_evidence_nodes
```

**Why:** Missing fields are not just "nothing" — they're explicit negative evidence. This ensures the graph captures what was NOT found, not just what was found.

**Test:** Generate a report with not-found fields. Verify `negative_evidence` list is populated with nodes for each not-found field.

---

### 9.2 Graph Integrity Summary

**File:** `prompt_matrix/services/v1_orchestrator.py`, in `build_report_timed`

**Requirement:** Include a graph integrity summary in the report.

**Fix:**
```python
def compute_graph_integrity(fields: list, negative_evidence: list, tree_nodes: list) -> dict:
    """Compute graph integrity summary."""
    total_expected = len(fields) + len(negative_evidence)
    total_addressed = sum(1 for f in fields if f.get("node_id")) + \
                      sum(1 for n in negative_evidence if n.get("node_id"))
    
    orphan_fields = [f for f in fields if not f.get("node_id") and f.get("field_state") != "not_found"]
    orphan_negatives = [n for n in negative_evidence if not n.get("node_id")]
    
    return {
        "total_nodes": total_expected,
        "addressed_nodes": total_addressed,
        "unaddressed_nodes": total_expected - total_addressed,
        "orphan_fields": len(orphan_fields),
        "orphan_negatives": len(orphan_negatives),
        "orphan_node_ids": [f.get("name") for f in orphan_fields] + \
                           [n.get("field_name") for n in orphan_negatives],
        "integrity_score": round(total_addressed / max(1, total_expected), 3),
        "integrity_flags": [
            "orphan_fields" if orphan_fields else None,
            "orphan_negatives" if orphan_negatives else None,
        ],
    }

# In build_report_timed:
report["graph_integrity"] = compute_graph_integrity(fields, negative_evidence_nodes, tree_nodes)
```

**Why:** The graph integrity summary shows how many nodes are properly addressed vs orphaned. This is critical for ensuring the graph is complete and correct.

**Test:** Generate a report with some orphan fields (missing node_id). Verify `graph_integrity` shows the orphans.

---

### 9.3 Orphan Detection

**Requirement:** Detect and flag orphan nodes (nodes that exist in the graph but aren't addressed by any field).

**Fix:**
```python
def detect_orphans(tree_nodes: list, fields: list, negative_evidence: list) -> list[dict]:
    """Detect nodes in the tree that aren't addressed by any field."""
    addressed_node_ids = set()
    for f in fields:
        if f.get("node_id"):
            addressed_node_ids.add(f["node_id"])
    for n in negative_evidence:
        if n.get("node_id"):
            addressed_node_ids.add(n["node_id"])
    
    orphans = []
    for node in tree_nodes:
        node_id = node.get("id")
        if node_id and node_id not in addressed_node_ids:
            orphans.append({
                "node_id": node_id,
                "type": node.get("type"),
                "content_preview": str(node.get("content") or node.get("title") or "")[:100],
                "reason": "Not addressed by any field",
            })
    
    return orphans

# In build_report_timed:
report["orphans"] = detect_orphans(tree_nodes, fields, negative_evidence_nodes)
```

**Why:** Orphans are nodes that exist in the document but aren't captured by any field. Detecting them ensures the extraction is complete.

**Test:** Generate a report with a tree that has unaddressed nodes. Verify `orphans` list is populated.

---

### 9.4 Node-Address Consistency Across Rerun/Export/Replay

**Requirement:** When rerun happens, the node addresses must be preserved or updated consistently. When export happens, the node addresses must match the replay.

**Fix:**
```python
def verify_node_address_consistency(report: dict, replay_state: dict) -> list[str]:
    """Verify that node addresses are consistent across report and replay."""
    issues = []
    
    report_fields = report.get("fields") or []
    replay_fields = replay_state.get("fields") or []
    
    report_field_map = {f.get("name"): f for f in report_fields}
    replay_field_map = {f.get("name"): f for f in replay_fields}
    
    for name in report_field_map:
        report_field = report_field_map[name]
        replay_field = replay_field_map.get(name)
        
        if replay_field:
            # Check node_id consistency
            report_node_id = report_field.get("node_id")
            replay_node_id = replay_field.get("node_id")
            if report_node_id != replay_node_id:
                issues.append(f"node_id mismatch for {name}: report={report_node_id} vs replay={replay_node_id}")
            
            # Check segment consistency
            report_segment = report_field.get("segment")
            replay_segment = replay_field.get("segment")
            if report_segment != replay_segment:
                issues.append(f"segment mismatch for {name}: report={report_segment} vs replay={replay_segment}")
    
    return issues
```

**Why:** Rerun should preserve or update node addresses consistently. If the replay has different node addresses than the report, something is wrong.

**Test:** Run rerun, verify node addresses in report match replay.

---

## Part 9.5: JDF Handling for Pictures, Signatures, Tables

### How JDF Handles Different Content Types

**Requirement:** It must be clear how the JDF (JSON Document Format) handles pictures, signatures, and tables.

**Current:** JDF handling is mentioned in passing (Phase 3.1 for tables, Phase 9.4 for node addresses) but not clearly explained as a whole.

**Clarification:**

**JDF Structure for Different Content Types:**

```python
# JDF is a tree structure. Different content types are represented as different node types:

JDF_TREE = {
    "type": "document",
    "id": "doc-root-abc123",
    "children": [
        # --- TEXT PAGE NODES ---
        {
            "type": "page",
            "id": "page-1",
            "content": "Policy Number: ABC-123456789\nInsured Name: John Smith\n...",
            "children": [
                # --- TEXT PARAGRAPH NODES ---
                {
                    "type": "paragraph",
                    "id": "p1e1",  # paragraph 1, element 1
                    "content": "Policy Number: ABC-123456789",
                    "elements": [
                        {"id": "p1e1a1", "content": "Policy Number:"},
                        {"id": "p1e1a2", "content": "ABC-123456789"},
                    ]
                },
                # --- TABLE NODES ---
                {
                    "type": "table",
                    "id": "table-1",
                    "headers": ["Coverage", "Limit"],
                    "rows": [
                        ["Bodily Injury", "$100,000"],
                        ["Property Damage", "$50,000"],
                    ],
                    "caption": "Coverage Limits",
                },
            ]
        },
        # --- PICTURE PAGE NODES ---
        {
            "type": "page",
            "id": "page-2",
            "content": "",  # Pictures don't have text content (or minimal OCR text)
            "children": [
                # --- PICTURE NODES ---
                {
                    "type": "picture",
                    "id": "pic-1",
                    "uri": "page-2-image-1.png",  # or base64, or reference to stored image
                    "analysis": {  # ← picture analysis results (from vision_service)
                        "objects": [
                            {"type": "vehicle", "position": "front", "damage": "front bumper crack"},
                            {"type": "vehicle", "position": "rear", "damage": "rear light broken"},
                        ],
                        "conditions": {"road": "wet", "weather": "rainy"},
                        "summary": "Two vehicles, front and rear damage, wet road conditions",
                    },
                    "quality": {  # ← picture quality assessment
                        "score": 0.85,
                        "issues": [],
                    },
                    "metadata": {
                        "width": 3000,
                        "height": 2000,
                        "orientation": "landscape",
                    }
                },
            ]
        },
        # --- SIGNATURE PAGE NODES ---
        {
            "type": "page",
            "id": "page-3",
            "content": "Authorized Signature: ____________________",
            "children": [
                # --- SIGNATURE NODES ---
                {
                    "type": "signature",
                    "id": "sig-1",
                    "state": "present_clear",  # ← finer signature state
                    "present": True,
                    "legible": True,
                    "basis": "visual inspection + OCR",
                    "confidence": 0.95,
                    "verification_confidence": 0.85,
                    "quality_flags": [],
                },
            ]
        },
    ]
}
```

**Key Points:**

1. **Tables in JDF:**
   - Table nodes have `type: "table"`
   - Structure: `headers`, `rows`, `caption`
   - Tables are referenced by their node_id in field extraction
   - Table quality is assessed separately

2. **Pictures in JDF:**
   - Picture nodes have `type: "picture"`
   - Structure: `uri` (reference to image), `analysis` (vision results), `quality` (quality assessment), `metadata` (dimensions, orientation)
   - Pictures are analyzed by vision_service and the results are stored in the `analysis` field
   - Picture quality is assessed separately

3. **Signatures in JDF:**
   - Signature nodes have `type: "signature"`
   - Structure: `state` (finer states: present_clear, present_ambiguous, missing, stamp, printed_name, etc.), `present`, `legible`, `basis`, `confidence`, `verification_confidence`
   - Signatures are assessed by quality_probe and the results are stored in the signature node

4. **Node Addressing:**
   - Each node has a unique `id`
   - Fields reference nodes by `node_id` (e.g., `field-abc123-policy_number` references text in `page-1`)
   - Tables reference their own node_id (e.g., `table-abc123-coverage`)
   - Pictures reference their own node_id (e.g., `pic-abc123-accident_scene`)
   - Signatures reference their own node_id (e.g., `sig-abc123-signature`)

**How This Relates to Fields:**

```python
# When a field is extracted, it references the JDF node it came from:

field = {
    "name": "policy_number",
    "value": "ABC-123456789",
    "node_id": "p1e1a2",  # ← references the specific element in JDF
    "segment": 0,
    "source": "label",
    "method": "label_pass",
}

# When a table is extracted:

field = {
    "name": "coverage_limit_bodily_injury",
    "value": "$100,000",
    "node_id": "table-1",  # ← references the table node
    "table_header": "Coverage",
    "table_column_index": 0,
    "source": "table",
    "method": "table_extraction",
}

# When a picture is analyzed:

vision_field = {
    "name": "scene_summary",
    "value": "Two vehicles, front and rear damage, wet road conditions",
    "node_id": "pic-1",  # ← references the picture node
    "source": "vision",
    "method": "vision_extraction",
    "analysis_type": "accident_scene",
}
```

**Why:** This clarifies how JDF handles different content types and how fields reference JDF nodes.

**Test:** Generate a report with tables, pictures, signatures. Verify the JDF structure is correct and fields reference the correct nodes.

---

## Part 10: Stable IDs and Pointer Stability



### 10.1 Stable document_id

**Requirement:** The `document_id` must be stable across revisions and reruns.

**Current:** `report["document_id"] = result.get("document_id")` — depends on the parse result.

**Fix:**
```python
# Ensure document_id is set and stable
if not report.get("document_id"):
    report["document_id"] = f"doc-{uuid.uuid4().hex[:16]}"

# document_id should NEVER change across revisions of the same document
# If a revision is created, it should reference the same document_id
```

**Why:** The document_id is the key identifier for a document. It must be stable so that revisions, reruns, and exports can all reference the same document.

**Test:** Create a revision of a document. Verify document_id is the same.

---

### 10.2 Stable Node Pointers Across Revisions

**Requirement:** When a revision is created, the node pointers (node_ids) should be stable across revisions.

**Fix:**
```python
def preserve_node_pointers(report: dict, revision_report: dict) -> list[str]:
    """Verify that node pointers are preserved across revisions."""
    issues = []
    
    report_fields = {f.get("name"): f for f in report.get("fields") or []}
    revision_fields = {f.get("name"): f for f in revision_report.get("fields") or []}
    
    for name in report_fields:
        if name in revision_fields:
            report_node_id = report_fields[name].get("node_id")
            revision_node_id = revision_fields[name].get("node_id")
            
            if report_node_id and revision_node_id:
                if report_node_id != revision_node_id:
                    issues.append(f"node pointer changed for {name}: {report_node_id} -> {revision_node_id}")
    
    return issues
```

**Why:** Node pointers should be stable so that the graph structure is preserved across revisions. If a node pointer changes, it should be flagged.

**Test:** Create a revision. Verify node pointers are preserved.

---

### 10.3 Replay/Export Preserving Field Anchors

**Requirement:** When replay or export happens, the field anchors (node_ids, segments) must be preserved.

**Fix:**
```python
def verify_field_anchors_preserved(report: dict, exported_report: dict) -> list[str]:
    """Verify that field anchors are preserved in export."""
    issues = []
    
    report_fields = {f.get("name"): f for f in report.get("fields") or []}
    exported_fields = {f.get("name"): f for f in exported_report.get("fields") or []}
    
    for name in report_fields:
        if name in exported_fields:
            report_field = report_fields[name]
            exported_field = exported_fields[name]
            
            # Check node_id
            if report_field.get("node_id") != exported_field.get("node_id"):
                issues.append(f"node_id lost for {name} in export")
            
            # Check segment
            if report_field.get("segment") != exported_field.get("segment"):
                issues.append(f"segment lost for {name} in export")
            
            # Check grounding
            if report_field.get("grounding_quote") != exported_field.get("grounding_quote"):
                issues.append(f"grounding_quote lost for {name} in export")
    
    return issues
```

**Why:** Export should preserve all the important field anchors. If grounding quotes or node_ids are lost in export, the export is incomplete.

**Test:** Export a report. Verify all field anchors are preserved.

---

## Part 11: First-Class Grounding Propagation

### 11.1 Where Grounding is Created

**Requirement:** Grounding must be created in a specific, traceable place.

**Current:** Grounding is mentioned but not clearly specified where it's created.

**Fix:**
```python
# Grounding is created in llm_extraction.py, in the llm_fill_missing or classify_with_model functions.
# The LLM returns a response that includes the extracted value AND the grounding quote/span.
# This response is parsed and the grounding_quote and grounding_span are added to the field.

# In llm_extraction.py:
def llm_fill_missing(field_spec, texts, completion, ...):
    """LLM fills missing fields. Returns field with grounding."""
    prompt = build_llm_prompt(field_spec, texts)
    response = completion(prompt)
    
    # Parse LLM response
    result = parse_llm_response(response)
    
    # Build field with grounding
    field = {
        "name": field_spec.name,
        "value": result.get("value"),
        "confidence": result.get("confidence", 0.8),
        "source": "llm",
        "method": "llm_fill",
        "grounding_quote": result.get("quote"),  # ← grounding_quote created here
        "grounding_span": result.get("span"),    # ← grounding_span created here
        "grounding_model": result.get("model_id"),
        "grounding_timestamp": _now(),
    }
    
    return field
```

**Why:** Grounding must be created in a specific place so it can be traced. The LLM response is parsed, and the quote/span are extracted and added to the field.

**Test:** Run LLM fill. Verify grounding_quote and grounding_span are populated.

---

### 11.2 How llm_extraction Output Becomes Field Fields

**Requirement:** The output of llm_extraction must be clearly mapped to field fields.

**Fix:**
```python
# In field_extractor.py, extract_segment_fields:

# After label pass, if LLM is available:
if completion:
    for spec in missing_specs:
        llm_field = llm_fill_missing(spec, texts, completion, ...)
        if llm_field:
            # Map llm_extraction output to field
            field = {
                "name": llm_field["name"],
                "value": llm_field["value"],
                "confidence": llm_field["confidence"],
                "source": llm_field.get("source", "llm"),
                "method": llm_field.get("method", "llm_fill"),
                "grounding_quote": llm_field.get("grounding_quote"),
                "grounding_span": llm_field.get("grounding_span"),
                "grounding_model": llm_field.get("grounding_model"),
                "grounding_timestamp": llm_field.get("grounding_timestamp"),
"field_state": "accepted" if llm_field.get("confidence", 0) >= 0.8 else "unverified",  # VALID field_state
"routing_action": "none" if llm_field.get("confidence", 0) >= 0.8 else "manual_review",  # VALID routing_action
"evidence_state": "found_verified" if llm_field.get("confidence", 0) >= 0.8 else "found_unverified",  # VALID evidence_state
"review_required": llm_field.get("confidence", 0) < 0.8,

                "review_required": llm_field.get("confidence", 0) <= 0.8,
                "value_quality": assess_value_quality(llm_field.get("value"), spec.field_type),
            }
            fields.append(field)
```

**Why:** The mapping from llm_extraction output to field fields is explicit and traceable.

**Test:** Run LLM fill. Verify the field has grounding_quote, grounding_span, and all mapped fields.

---

### 11.3 How Grounding is Serialized into the Report Model

**Requirement:** Grounding must be serialized into the report model correctly.

**Fix:**
```python
# In build_report_timed, when building the report:

for field in fields:
    report_field = {
        "name": field["name"],
        "value": field["value"],
        "field_state": field.get("field_state"),
        "routing_action": field.get("routing_action"),
        "review_required": field.get("review_required"),
        "confidence": field.get("confidence"),
        "provenance_confidence": field.get("provenance_confidence"),
        "verification_confidence": field.get("verification_confidence"),
        "value_quality": field.get("value_quality"),
        "source": field.get("source"),
        "method": field.get("method"),
        # Grounding fields — serialized explicitly
        "grounding_quote": field.get("grounding_quote"),
        "grounding_span": field.get("grounding_span"),
        "grounding_model": field.get("grounding_model"),
        "grounding_timestamp": field.get("grounding_timestamp"),
        # ... other fields
    }
    report_fields.append(report_field)

report["fields"] = report_fields
```

**Why:** Grounding fields are serialized explicitly, not lost in a generic serialization.

**Test:** Generate a report. Verify grounding_quote and grounding_span are in the serialized fields.

---

### 11.4 How UI Consumes Grounding

**Requirement:** The UI must consume grounding in a specific way.

**Fix:**
```javascript
// In the UI, when displaying a field:
function renderField(field) {
    const fieldElement = document.createElement("div");
    fieldElement.className = "field";
    
    // Show field name and value
    fieldElement.innerHTML = `
        <div class="field-name">${field.name}</div>
        <div class="field-value">${field.value || "—"}</div>
    `;
    
    // Show grounding if available
    if (field.grounding_quote) {
        const groundingElement = document.createElement("div");
        groundingElement.className = "grounding";
        groundingElement.innerHTML = `
            <span class="grounding-label">Evidence:</span>
            <span class="grounding-quote">"${field.grounding_quote}"</span>
            ${field.grounding_span ? `<span class="grounding-span">(page ${field.grounding_span.page})</span>` : ""}
        `;
        fieldElement.appendChild(groundingElement);
    }
    
    // Show confidence
    if (field.confidence !== undefined) {
        const confidenceElement = document.createElement("div");
        confidenceElement.className = "confidence";
        confidenceElement.innerHTML = `
            <span class="confidence-label">Confidence:</span>
            <span class="confidence-value">${Math.round(field.confidence * 100)}%</span>
        `;
        fieldElement.appendChild(confidenceElement);
    }
    
    return fieldElement;
}

// Click on grounding quote to highlight in document viewer
function onGroundingClick(field) {
    if (field.grounding_span) {
        documentViewer.highlightSpan({
            page: field.grounding_span.page,
            start: field.grounding_span.start,
            end: field.grounding_span.end,
        });
    }
}
```

**Why:** The UI consumes grounding in a specific, interactive way. Clicking on the grounding quote highlights the evidence in the document viewer.

**Test:** Load a report in the UI. Verify grounding quotes are displayed and clickable.

---

## Part 12: Extraction-Quality Split

### 12.1 Clear Split Between Provenance Confidence, Extraction Quality, Value Quality

**Requirement:** The three confidence/quality signals must be clearly separated and not conflated.

**Current:** The plan uses `value_quality` but doesn't clearly define the split between:
- `provenance_confidence` — how confident are we that this value came from the right place?
- `extraction_quality` — how well was this value extracted? (method, confidence of extraction)
- `value_quality` — how good is the value itself? (valid, garbage, header, etc.)

**Fix:**
```python
# In build_found_field, clearly separate the three signals:

field = {
    # Provenance confidence — how confident are we that this value came from the right place?
    "provenance_confidence": compute_provenance_confidence(field),
    "provenance_source": field.get("source"),  # "label", "table", "llm", "vision"
    "provenance_method": field.get("method"),  # "label_pass", "llm_fill", "table_extraction", "vision"
    
    # Extraction quality — how well was this value extracted?
    "extraction_quality": compute_extraction_quality(field),
    "extraction_confidence": field.get("confidence"),  # confidence of the extraction method
    "extraction_method": field.get("method"),
    
    # Value quality — how good is the value itself?
    "value_quality": assess_value_quality(field.get("value"), spec.field_type),
    "value_validity": "valid" if value_quality == "valid" else "invalid",
}

def compute_provenance_confidence(field: dict) -> float:
    """Compute provenance confidence based on source and method."""
    source = field.get("source") or "unknown"
    method = field.get("method") or "unknown"
    
    if source == "label" and method == "label_pass":
        return 0.9  # Label pass is reliable for provenance
    elif source == "table" and method == "table_extraction":
        return 0.8  # Table extraction is reliable but depends on table quality
    elif source == "llm" and method == "llm_fill":
        return 0.7  # LLM fill is good but not as reliable as label pass
    elif source == "vision" and method == "vision_extraction":
        return 0.6  # Vision extraction is emerging technology
    else:
        return 0.5  # Unknown provenance

def compute_extraction_quality(field: dict) -> dict:
    """Compute extraction quality assessment."""
    return {
        "confidence": field.get("confidence", 0.5),
        "method": field.get("method"),
        "parse_confidence": field.get("parse_confidence"),
        "ocr_confidence": field.get("ocr_confidence"),
        "assessment": "good" if field.get("confidence", 0) > 0.8 else "fair" if field.get("confidence", 0) > 0.5 else "poor",
    }

def assess_value_quality(value: str | None, field_type: str) -> str:
    """Assess the quality of the extracted value."""
    if value is None or value == "":
        return "empty"
    
    value_lower = str(value).lower()
    
    # Check for garbage/header/label patterns
    header_patterns = ["header", "label", "section", "subtitle", "caption"]
    if any(pattern in value_lower for pattern in header_patterns):
        return "header_or_label"
    
    # Check for address fragments
    if is_address_fragment(value):
        return "address_fragment"
    
    # Check for valid format based on field type
    if field_type == "money":
        if parse_money(value):
            return "valid"
        return "invalid_format"
    elif field_type == "number":
        if parse_number(value):
            return "valid"
        return "invalid_format"
    elif field_type == "date":
        if parse_date(value):
            return "valid"
        return "invalid_format"
    elif field_type == "name":
        if is_name(value):
            return "valid"
        return "invalid_format"
    else:
        # Text fields — check for garbage
        if len(str(value)) < 3:
            return "garbage"
        return "valid"
```

**Why:** The three signals are clearly separated:
- `provenance_confidence` — where did this value come from?
- `extraction_quality` — how well was it extracted?
- `value_quality` — how good is the value itself?

This prevents the confusion where provenance looks strong (1.0) but the value is garbage.

**Test:** Generate a report with garbage values. Verify:
- `provenance_confidence` might be high (0.9 for label pass)
- `extraction_quality.confidence` might be high (0.9 for label pass)
- `value_quality` is "header_or_label" or "garbage"
- The value is correctly identified as bad despite high provenance/extraction confidence

---

## Part 13: Mixed-Bundle / Page-Level Coverage

### 13.1 Page-Level Classification Coverage

**Requirement:** Show which pages were classified and what they were classified as.

**Fix:**
```python
# In build_report_timed, after classification:

page_classification = []
for i, page in enumerate(pages):
    page_text = texts[i] if i < len(texts) else ""
    page_type = classify_page(page_text) if page_text else "empty"
    page_classification.append({
        "page": i + 1,
        "type": page_type,
        "confidence": classify_confidence(page_text),
        "text_length": len(page_text),
        "flags": [flag for flag in page.get("flags") or []],
    })

report["page_classification"] = page_classification
```

**Why:** Users can see which pages were classified and what they contain. This is important for mixed bundles where different pages may be different document types.

**Test:** Generate a report with a mixed bundle. Verify `page_classification` shows each page's classification.

---

### 13.2 Mixed-Bundle Summaries

**Requirement:** For mixed bundles, provide a summary of the bundle composition.

**Fix:**
```python
# In build_report_timed, for mixed bundles:

if mixed:
    bundle_summary = {
        "total_pages": len(pages),
        "total_documents": len(documents),
        "documents": [
            {
                "index": seg["index"],
                "document_type": seg.get("document_type"),
                "pages": seg.get("pages_searched"),
                "fields_found": seg.get("fields_found"),
                "fields_total": seg.get("fields_total"),
                "confidence": seg.get("confidence"),
            }
            for seg in documents
        ],
        "material_type": "mixed_bundle",
        "modality": "mixed",
    }
    report["bundle_summary"] = bundle_summary
```

**Why:** Mixed bundles are complex. A summary helps users understand what's in the bundle and how it was processed.

**Test:** Generate a report with a mixed bundle. Verify `bundle_summary` is populated.

---

### 13.3 "What Pages Were Searched" Reporting

**Requirement:** For each field, show which pages were searched for that field.

**Fix:**
```python
# In extract_segment_fields, for each field:

field["pages_searched"] = list(seg["pages"])  # the pages in this segment
field["pages_with_hits"] = [i + 1 for i, text in enumerate(texts) if text and spec.label_regex.search(text)]

# In build_report_timed, add to field:
report_field["pages_searched"] = field.get("pages_searched")
report_field["pages_with_hits"] = field.get("pages_with_hits")
```

**Why:** Users can see where the system looked for a field and where it found hits. This is important for debugging extraction issues.

**Test:** Generate a report. Verify fields have `pages_searched` and `pages_with_hits`.

---

## Part 14: Signature Taxonomy

### 14.1 Finer Signature States

**Requirement:** The signature assessment should use finer states than just "present" / "missing".

**Current:** Signature states are limited. Need finer granularity.

**Fix:**
```python
# In quality_probe.py, assess_signature:

SIGNATURE_STATES = [
    "present_clear",    # Signature is clearly present and legible
    "present_ambiguous", # Signature is present but ambiguous (could be a mark)
    "missing",          # No signature found
    "stamp",            # Signature is a stamp (not handwritten)
    "printed_name",     # Only printed name, no signature
    "unreadable",       # Signature is present but unreadable
    "faint",            # Signature is present but faint
    "incomplete",       # Signature is present but incomplete
    "questionable",     # Signature is questionable (looks suspicious)
]

def assess_signature(page_text: str, visual: dict | None = None, ocr_lines: list | None = None, page: int | None = None) -> dict[str, Any]:
    """Assess signature quality with finer states."""
    
    # ... existing assessment logic ...
    
    # Map assessment to finer state
    if signature_present and is_clear:
        state = "present_clear"
    elif signature_present and is_ambiguous:
        state = "present_ambiguous"
    elif not signature_present:
        state = "missing"
    elif is_stamp:
        state = "stamp"
    elif is_printed_name_only:
        state = "printed_name"
    elif is_unreadable:
        state = "unreadable"
    elif is_faint:
        state = "faint"
    elif is_incomplete:
        state = "incomplete"
    elif is_questionable:
        state = "questionable"
    else:
        state = "missing"
    
    return {
        "state": state,
        "present": signature_present,
        "legible": is_legible,
        "basis": basis,
        "quality_flags": quality_flags,
    }
```

**Why:** Finer signature states provide more detail about the signature's condition. This is important for compliance and review.

**Test:** Generate a report with signatures in various states. Verify the signature state is one of the finer states.

---

### 14.2 Signature States in Field Output

**Requirement:** Signature states should be reflected in the signature field output.

**Fix:**
```python
# In build_report_timed, for the signature field:

sig_field = next((f for f in fields if f.get("field_type") == "signature"), None)
if sig_field:
    sig_quality = sig_field.get("signature_quality") or {}
    report["signature_quality"] = {
        "state": sig_quality.get("state", "missing"),
        "present": sig_quality.get("present"),
        "legible": sig_quality.get("legible"),
        "basis": sig_quality.get("basis"),
        "confidence": sig_field.get("confidence"),
        "verification_confidence": sig_field.get("verification_confidence"),
    }
```

**Why:** The signature field output should include the finer signature state.

**Test:** Generate a report with a signature field. Verify `signature_quality.state` is one of the finer states.

---

## Part 15: Review-Mode vs Prompt-Mode Separation

### 15.1 Review Mode Separate from Prompt Mode

**Requirement:** The UI must have separate modes for review and prompt/editing.

**Fix:**
```javascript
// In the UI:

const REVIEW_MODE = "review";
const PROMPT_MODE = "prompt";

let currentMode = REVIEW_MODE;

function setMode(mode) {
    currentMode = mode;
    updateUIForMode();
}

function updateUIForMode() {
    if (currentMode === REVIEW_MODE) {
        // Review mode: read-only viewing, flagging fields for review
        document.getElementById("field-editor").style.display = "none";
        document.getElementById("field-list").style.display = "block";
        document.getElementById("mode-indicator").textContent = "Review Mode";
    } else if (currentMode === PROMPT_MODE) {
        // Prompt mode: editing, prompting for values
        document.getElementById("field-editor").style.display = "block";
        document.getElementById("field-list").style.display = "none";
        document.getElementById("mode-indicator").textContent = "Prompt Mode";
    }
}

// Switch between modes
document.getElementById("review-mode-btn").onclick = () => setMode(REVIEW_MODE);
document.getElementById("prompt-mode-btn").onclick = () => setMode(PROMPT_MODE);
```

**Why:** Review mode and prompt mode are different workflows. Review mode is for viewing and flagging. Prompt mode is for editing and prompting. They should be separate.

**Test:** Switch between modes. Verify UI changes appropriately.

---

### 15.2 Source Preview Side-by-Side on Correction

**Requirement:** When correcting a field, show the source document side-by-side.

**Fix:**
```javascript
// In the UI, when editing a field:

function editField(field) {
    // Show editor panel
    const editorPanel = document.getElementById("field-editor");
    editorPanel.style.display = "block";
    
    // Populate editor with field data
    editorPanel.querySelector("#field-name").textContent = field.name;
    editorPanel.querySelector("#field-value").value = field.value || "";
    
    // Show source preview side-by-side
    const sourcePreview = document.getElementById("source-preview");
    sourcePreview.style.display = "block";
    
    // Highlight the grounding span in the source preview
    if (field.grounding_span) {
        sourcePreview.highlightSpan({
            page: field.grounding_span.page,
            start: field.grounding_span.start,
            end: field.grounding_span.end,
        });
    }
    
    // Show the grounding quote
    if (field.grounding_quote) {
        sourcePreview.showQuote(field.grounding_quote);
    }
}
```

**Why:** When correcting a field, the user should see the source document side-by-side to verify the correction. The grounding quote and span provide context.

**Test:** Edit a field. Verify source preview is shown side-by-side with the editor.

---

### 15.3 List-First Navigation Before Detail

**Requirement:** The UI should show a list of fields first, then allow drilling into detail.

**Fix:**
```javascript
// In the UI:

// Field list panel (always visible)
const fieldListPanel = document.getElementById("field-list");
fieldListPanel.innerHTML = "";

for (const field of report.fields) {
    const fieldItem = document.createElement("div");
    fieldItem.className = "field-item";
    fieldItem.textContent = field.name;
    fieldItem.onclick = () => showFieldDetail(field);
    fieldListPanel.appendChild(fieldItem);
}

// Detail panel (shown when a field is selected)
const detailPanel = document.getElementById("field-detail");
detailPanel.style.display = "none";

function showFieldDetail(field) {
    detailPanel.style.display = "block";
    detailPanel.innerHTML = renderFieldDetail(field);
}

// List-first navigation: user sees list first, clicks to see detail
```

**Why:** List-first navigation lets users see all fields at a glance, then drill into detail as needed. This is more efficient than showing all details at once.

**Test:** Load a report. Verify field list is shown first. Click a field. Verify detail is shown.

---

## Part 16: End-to-End Proof

### 16.1 Single Test Proving Red-Hat Changes Output

**Requirement:** There must be a single test that proves:
- Red-Hat critique happens
- Rerun is triggered
- Node/field output changes
- The updated artifact is reflected in JSON/UI/export/replay together

**Fix:**
```python
def test_end_to_end_redhat_impact():
    """End-to-end test proving Red-Hat changes output."""
    
    # Step 1: Parse a document and generate initial report
    initial_report = parse_and_build_report(test_document)
    
    # Step 2: Verify initial state
    assert initial_report["laya"]["status"] == "not_started"
    assert initial_report["verification"]["z3_status"] == "not_run"
    assert initial_report["replay"]["attempts"] == 0
    
    # Step 3: Run LAYA/Red-Hat
    laya_result = run_laya_review(initial_report, project_id)
    
    # Step 4: Verify LAYA completed
    assert laya_result["laya"]["status"] == "completed"
    assert len(laya_result["laya"]["findings"]) > 0  # Red-Hat found something
    
    # Step 5: Trigger rerun based on LAYA findings
    rerun_result = trigger_rerun(initial_report, laya_result, project_id)
    
    # Step 6: Verify rerun happened
    assert rerun_result["replay"]["attempts"] > 0
    assert rerun_result["replay"]["improved"] == True  # or False if no improvement
    
    # Step 7: Verify output changed
    # Compare initial and final reports
    initial_fields = {f["name"]: f for f in initial_report["fields"]}
    final_fields = {f["name"]: f for f in rerun_result["fields"]}
    
    changed_fields = []
    for name in initial_fields:
        if name in final_fields:
            if initial_fields[name].get("value") != final_fields[name].get("value"):
                changed_fields.append(name)
            elif initial_fields[name].get("confidence") != final_fields[name].get("confidence"):
                changed_fields.append(name)
            elif initial_fields[name].get("field_state") != final_fields[name].get("field_state"):
                changed_fields.append(name)
    
    assert len(changed_fields) > 0  # Something changed
    
    # Step 8: Verify JSON/UI/export/replay agreement
    agreement_issues = verify_agreement(
        rerun_result,  # JSON
        simulate_ui_state(rerun_result),  # UI
        simulate_export_state(rerun_result),  # Export
        rerun_result,  # Replay
    )
    assert len(agreement_issues) == 0  # All agree
    
    # Step 9: Verify artifact hash
    assert verify_artifact_integrity(rerun_result)  # Hash matches
    
    # Step 10: Verify graph integrity
    assert rerun_result["graph_integrity"]["integrity_score"] > 0.9  # High integrity
    assert len(rerun_result["graph_integrity"]["orphan_fields"]) == 0  # No orphans
    
    return {
        "initial_report": initial_report,
        "laya_result": laya_result,
        "rerun_result": rerun_result,
        "changed_fields": changed_fields,
        "agreement_issues": agreement_issues,
        "passed": True,
    }
```

**Why:** This single test proves that the entire pipeline works end-to-end:
- LAYA/Red-Hat runs and finds something
- Rerun is triggered based on LAYA findings
- The output changes (fields are corrected/improved)
- JSON, UI, export, and replay all agree
- Artifact integrity is maintained
- Graph integrity is high

**Test:** Run this test. Verify it passes.

---

### 16.2 Single Test Proving Z3 Changes Output

**Requirement:** There must be a single test that proves Z3 verification changes output.

**Fix:**
```python
def test_end_to_end_z3_impact():
    """End-to-end test proving Z3 changes output."""
    
    # Step 1: Parse a document with compliance-bound fields
    initial_report = parse_and_build_report(test_document_with_compliance_fields)
    
    # Step 2: Verify initial state
    assert initial_report["verification"]["z3_status"] == "not_run"
    
    # Step 3: Run Z3 verification
    z3_result = run_z3_verification(initial_report["fields"], initial_report["document_type"])
    
    # Step 4: Verify Z3 completed
    assert z3_result["status"] in ("pass", "violation")
    
    # Step 5: If violation, verify conflicts are added
    if z3_result["status"] == "violation":
        assert len(z3_result["violations"]) > 0
        assert len(initial_report["conflicts"]) >= len(z3_result["violations"])
    
    # Step 6: Verify JSON/UI/export/replay agreement
    agreement_issues = verify_agreement(...)
    assert len(agreement_issues) == 0
    
    return {"passed": True}
```

**Why:** Proves Z3 verification works and affects output.

---

### 16.3 Single Test Proving Rerun Changes Output

**Requirement:** There must be a single test that proves rerun changes output.

**Fix:**
```python
def test_end_to_end_rerun_impact():
    """End-to-end test proving rerun changes output."""
    
    # Step 1: Parse a document with low-confidence fields
    initial_report = parse_and_build_report(test_document_with_low_confidence)
    
    # Step 2: Identify low-confidence fields
    low_conf_fields = identify_low_confidence_fields(initial_report["fields"])
    assert len(low_conf_fields) > 0  # Must have low-confidence fields
    
    # Step 3: Trigger rerun
    rerun_result = trigger_rerun(initial_report, project_id)
    
    # Step 4: Verify rerun happened
    assert rerun_result["replay"]["attempts"] > 0
    
    # Step 5: Verify output changed
    initial_fields = {f["name"]: f for f in initial_report["fields"]}
    final_fields = {f["name"]: f for f in rerun_result["fields"]}
    
    improved_fields = []
    for name in low_conf_fields:
        if name in final_fields:
            initial_conf = low_conf_fields[name].get("confidence", 0)
            final_conf = final_fields[name].get("confidence", 0)
            if final_conf > initial_conf:
                improved_fields.append(name)
    
    assert len(improved_fields) > 0  # Some fields improved
    
    # Step 6: Verify JSON/UI/export/replay agreement
    assert len(verify_agreement(...)) == 0
    
    return {"passed": True}
```

**Why:** Proves rerun works and improves output.

---

## Part 17: Updated Priority and Sequencing

### Must do first (Phase 1 + Phase 2.1 + Phase 2.0)

These give the biggest visible improvement quickly and set the foundation:

1. **2.0 Key Distinction** — understand that we're wiring existing pieces, not inventing new ones
2. **1.1 Fix provenance_confidence** — garbage values no longer look perfect
3. **1.2 Fix field_state/routing_action** — better routing for suspect fields
4. **2.1 Enable LLM grounding** — enables grounding quotes, LLM fill, better classification

### High priority (Phase 2.2, 2.3, 2.4 + Phase 8 + Phase 11)

These enable the execution model and enforce truth:

5. **2.2 Wire LAYA** — LAYA findings in output
6. **2.3 Wire Z3** — Z3 verification in output
7. **2.4 Wire rerun loop** — iterative improvement
8. **8.1-8.4 Artifact integrity gates** — canonical snapshot, hash gate, export refusal
9. **11.1-11.4 First-class grounding propagation** — where grounding is created, how it's mapped, serialized, consumed

### Medium priority (Phase 1.3-1.6, Phase 3, Phase 4, Phase 9, Phase 10, Phase 12)

These improve quality, extensibility, and integrity:

10. **1.3 Verify review_summary** — correct separation
11. **1.4 Verify JSON serialization** — all fields present
12. **1.5 Classification confidence in output** — transparency
13. **1.6 matched_keywords consistency** — correct classification data
14. **Phase 3 Table handling** — better table extraction
15. **Phase 4 Schema extensibility** — new doc types without code changes
16. **9.1-9.4 Graph integrity / negative evidence / orphan handling** — complete graph
17. **10.1-10.3 Stable IDs and pointer stability** — stable document_id, node pointers, field anchors
18. **12.1 Extraction-quality split** — clear separation of provenance, extraction, value quality
19. **13.1-13.3 Mixed-bundle/page-level coverage** — page classification, bundle summary, pages searched

### Important but later (Phase 5, Phase 6, Phase 14, Phase 15)

These add new capabilities and polish:

20. **Phase 5 Picture handling** — vision model integration (biggest new capability, most effort)
21. **Phase 6 UI connections** — enterprise look and usability
22. **14.1-14.2 Signature taxonomy** — finer signature states
23. **15.1-15.3 Review-mode vs prompt-mode separation** — separate modes, side-by-side preview, list-first navigation

### Critical verification (Phase 16)

This proves everything works end-to-end:

24. **16.1-16.3 End-to-end proof tests** — single tests proving Red-Hat, Z3, rerun change output and all artifacts agree

---

## Part 18: Updated What Success Looks Like

After all phases are implemented, the output should show:

**For a typical auto policy document:**
```json
{
  "report_id": "pr-...",
  "document_id": "doc-abc123",  // ← stable document_id
  "artifact_hash": "a1b2c3d4e5f6...",  // ← canonical hash
  "artifact_version": "1.0",
  "document_type": "auto_policy",
  "classification": {
    "document_type": "auto_policy",
    "confidence": 0.89,
    "basis": "keyword match: policy, insured, vehicle, premium",
    "matched_keywords": ["policy", "insured", "vehicle", "premium"]
  },
  "fields": [
    {
      "name": "policy_number",
      "value": "ABC-123456789",
"field_state": "accepted",  # VALID field_state
"routing_action": "none",  # VALID routing_action
"evidence_state": "found_verified",  # VALID evidence_state

      "review_required": false,
      "confidence": 0.95,
      "provenance_confidence": 0.9,  // ← provenance (source reliability)
      "extraction_quality": {          // ← extraction quality
        "confidence": 0.95,
        "method": "label_pass",
        "assessment": "good"
      },
      "value_quality": "valid",       // ← value quality
      "verification_confidence": 0.85,
      "source": "label",
      "method": "label_pass",
      "grounding_quote": "Policy Number: ABC-123456789",  // ← grounding
      "grounding_span": {"page": 1, "start": 150, "end": 170},
      "grounding_model": "gemini-pro",
      "grounding_timestamp": "2026-09-27T15:00:00Z",
      "pages_searched": [1],
      "pages_with_hits": [1],
      "node_id": "field-abc123-policy_number",  // ← stable node pointer
      "segment": 0,
    },
    {
"field_state": "accepted",  # VALID field_state
"routing_action": "none",  # VALID routing_action
"evidence_state": "found_verified",  # VALID evidence_state


      "review_required": false,
      "confidence": 0.92,
      "provenance_confidence": 0.9,
      "extraction_quality": {"confidence": 0.92, "method": "label_pass", "assessment": "good"},
      "value_quality": "valid",
      "verification_confidence": 0.85,
      "source": "label",
      "method": "label_pass",
      "grounding_quote": "Insured Name: John Smith",
      "grounding_span": {"page": 1, "start": 200, "end": 220},
      "grounding_model": "gemini-pro",
      "grounding_timestamp": "2026-09-27T15:00:01Z",
      "pages_searched": [1],
      "pages_with_hits": [1],
      "node_id": "field-abc123-insured_name",
      "segment": 0,
    },
    {
      "name": "vin",
      "value": null,
      "field_state": "not_found",
      "routing_action": "field_not_found",
      "review_required": false,
      "confidence": 0.0,
      "provenance_confidence": null,
      "extraction_quality": {"confidence": 0.0, "method": "label_pass", "assessment": "poor"},
      "value_quality": "empty",
      "verification_confidence": null,
      "source": "label",
      "method": "label_pass",
      "grounding_quote": null,
      "grounding_span": null,
      "pages_searched": [1],
      "pages_with_hits": [],
      "node_id": "field-abc123-vin",
      "segment": 0,
      "reason": "No value found under label 'VIN' or 'Vehicle Identification Number'"
    }
  ],
  "negative_evidence": [  // ← negative evidence nodes for missing fields
    {
      "id": "ne-abc123-vin",
      "type": "negative_evidence",
      "field_name": "vin",
      "status": "not_found",
      "reason": "No value found under label 'VIN' or 'Vehicle Identification Number'",
      "confidence": 0.0,
      "segment": 0,
      "document_id": "doc-abc123",
      "created_at": "2026-09-27T15:00:02Z",
    }
  ],
  "graph_integrity": {  // ← graph integrity summary
    "total_nodes": 4,
    "addressed_nodes": 4,
    "unaddressed_nodes": 0,
    "orphan_fields": 0,
    "orphan_negatives": 0,
    "orphan_node_ids": [],
    "integrity_score": 1.0,
    "integrity_flags": [],
  },
  "orphans": [],  // ← no orphan nodes
  "laya": {
    "status": "completed",
    "findings": [
      {"title": "...", "content": "...", "severity": "low", "suggested_fix": "..."}
    ],
    "completed_at": "2026-09-27T15:00:00Z"
  },
  "verification": {
    "z3_status": "pass",
    "z3_violation_count": 0,
    "redhat_status": "completed",
    "redhat_findings": [...]  // actual findings
  },
  "replay": {
    "attempts": 1,  // ← actual rerun attempts
    "improved": true,  // ← actual improvement
    "history": [  // ← actual history
      {"attempt": 0, "approach": "initial_label_pass", "low_confidence_count": 2, "improvement": null},
      {"attempt": 1, "approach": "llm_fill", "low_confidence_count": 0, "improvement": true},
    ],
    "final_low_confidence_count": 0
  },
  "review_summary": {
    "total_fields": 15,
    "fields_found": 12,
    "fields_not_found": 3,
    "fields_review": 2,
    "fields_suspect": 0,
    "fields_auto_accepted": 10
  },
  "page_classification": [  // ← page-level classification
    {"page": 1, "type": "auto_policy", "confidence": 0.95, "text_length": 2000, "flags": []},
    {"page": 2, "type": "auto_policy", "confidence": 0.90, "text_length": 1500, "flags": []},
  ],
  "bundle_summary": null,  // ← null for single documents
  "tables": [
    {
      "node_id": "table-abc123-coverage",
      "headers": ["Coverage", "Limit"],
      "rows": [["Bodily Injury", "$100,000"], ["Property Damage", "$50,000"]],
      "quality": {"score": 0.95, "issues": []}
    }
  ],
  "vision_fields": [  // ← picture analysis results
    {
      "name": "scene_summary",
      "value": "Two vehicles involved in collision. Vehicle 1 (front) has front bumper crack and headlight damage. Vehicle 2 (rear) has rear light broken and trunk dent. Road conditions: wet. Weather: rainy. No traffic controls visible.",
      "node_id": "pic-abc123-accident_scene",
      "source": "vision",
      "method": "vision_extraction",
      "analysis_type": "accident_scene",
      "confidence": 0.85,
      "value_quality": "valid",
      "pages_searched": [2],
      "pages_with_hits": [2],
    },
    {
      "name": "vehicle_count",
      "value": "2",
      "node_id": "pic-abc123-accident_scene",
      "source": "vision",
      "method": "vision_extraction",
      "analysis_type": "accident_scene",
      "confidence": 0.90,
      "value_quality": "valid",
    },
    {
      "name": "damage_description_visual",
      "value": "Vehicle 1: front bumper crack, headlight damage. Vehicle 2: rear light broken, trunk dent.",
      "node_id": "pic-abc123-accident_scene",
      "source": "vision",
      "method": "vision_extraction",
      "analysis_type": "accident_scene",
      "confidence": 0.80,
      "value_quality": "valid",
    }
  ],
  "picture_quality": [  // ← picture quality assessment
    {
      "node_id": "pic-abc123-accident_scene",
      "quality_score": 0.85,
      "issues": [],
      "width": 3000,
      "height": 2000,
      "orientation": "landscape",
    }
  ],
  "signature_quality": {  // ← signature quality with finer states
    "state": "present_clear",
    "present": true,
    "legible": true,
    "basis": "visual inspection + OCR",
    "confidence": 0.95,
    "verification_confidence": 0.85,
  },
  "quality_report": {
    "document_quality_score": 0.85,
    "quality_flags": ["mixed_bundle"],
    "low_quality_pages": [],
  }
}
```

**Key improvements over current output:**
1. `field_state` correctly shows `not_found` for missing fields, `accepted` for good fields

2. `provenance_confidence` is 0.9 for valid values (source reliability), lower for garbage
3. `extraction_quality` is clearly separated from `value_quality`
4. `value_quality` is "valid" for good values, "garbage"/"header_or_label" for bad values
5. `grounding_quote` and `grounding_span` provide evidence, with model and timestamp
6. `laya.status` is `"completed"` with actual findings
7. `verification.z3_status` is `"pass"` or `"violation"` with actual results
8. `replay` reflects actual execution (attempts > 0, history populated)
9. `review_summary` correctly separates not-found from review items
10. `negative_evidence` nodes for missing fields
11. `graph_integrity` summary with integrity score
12. `orphans` detected and flagged
13. `artifact_hash` for integrity checking
14. `document_id` is stable
15. `node_id` is stable across revisions
16. `pages_searched` and `pages_with_hits` for each field
17. `page_classification` for each page
18. `signature_quality.state` is one of the finer states
19. Classification confidence is shown and reflects actual uncertainty
20. Tables preserve structure with quality assessment

**And the pipeline enforces truth:**
- Artifact hash gate: if hash doesn't match, export is refused
- Agreement check: JSON/UI/export/replay must agree
- Graph integrity: orphans are flagged
- Node address consistency: replay must match report
- Field anchors preserved: export must preserve node_ids, grounding, segments

---

## Part 19: Updated Execution Checklist for Fable

- [ ] **2.0 Key Distinction** — understand wiring existing pieces vs inventing new
- [ ] **1.1 Fix `provenance_confidence` in `build_found_field`**
- [ ] **1.2 Fix `field_state`/`routing_action` for found-but-unparseable fields**
- [ ] **1.3 Verify `review_summary` separation (fix if needed)**
- [ ] **1.4 Verify JSON serialization includes all fields (add if missing)**
- [ ] **1.5 Add classification confidence to output (if not present)**
- [ ] **1.6 Fix `matched_keywords` inconsistency (fix if still broken)**
- [ ] **2.1 Wire `completion` (LLM grounding) into production**
- [ ] **2.2 Wire LAYA/Red-Hat into pipeline**
- [ ] **2.3 Wire Z3 verification into pipeline**
- [ ] **2.4 Wire rerun loop into pipeline**
- [ ] **3.1 Preserve table structure for extraction**
- [ ] **3.2 Add table-aware extraction for key fields**
- [ ] **3.3 Add table quality assessment**
- [ ] **4.1 Add schema registry**
- [ ] **4.2 Add dynamic field discovery for unknown types**
- [ ] **4.3 Handle extra intake parameters**
- [ ] **4.4 Add extensibility for new document types**
- [ ] **5.1 Add vision model integration (create `vision_service.py`)**
- [ ] **5.2 Add picture-to-structured-data extraction**
- [ ] **5.3 Wire picture analysis into pipeline**
- [ ] **5.4 Add picture-specific fields to taxonomy**
- [ ] **5.5 Add picture quality assessment**
- [ ] **6.1 UI — display field states clearly**
- [ ] **6.2 UI — display review summary**
- [ ] **6.3 UI — display execution status**
- [ ] **6.4 UI — display grounding evidence**
- [ ] **6.5 UI — handle picture analysis results**
- [ ] **6.6 UI — handle table results**
- [ ] **6.7 UI — handle rerun results**
- [ ] **6.8 UI — enterprise polish**
- [ ] **7.1 Verify output reflects execution**
- [ ] **7.2 Test with same documents that produced bad output**
- [ ] **7.3 Test with new document types**
- [ ] **7.4 Test with pictures**
- [ ] **7.5 Test with tables**
- [ ] **7.6 Test edge cases**
- [ ] **8.1 Canonical snapshot** — compute artifact hash for every report
- [ ] **8.2 Artifact hash gate** — verify hash before export
- [ ] **8.3 JSON/UI/export/replay agreement check** — check all agree
- [ ] **8.4 Export refusal on mismatch** — refuse to export if integrity fails
- [ ] **9.1 Negative evidence nodes for missing fields**
- [ ] **9.2 Graph integrity summary**
- [ ] **9.3 Orphan detection**
- [ ] **9.4 Node-address consistency across rerun/export/replay**
- [ ] **10.1 Stable document_id**
- [ ] **10.2 Stable node pointers across revisions**
- [ ] **10.3 Replay/export preserving field anchors**
- [ ] **11.1 Where grounding is created** — in llm_extraction, traceable
- [ ] **11.2 How llm_extraction output becomes field fields** — explicit mapping
- [ ] **11.3 How grounding is serialized into report model** — explicit serialization
- [ ] **11.4 How UI consumes grounding** — display and clickable
- [ ] **12.1 Extraction-quality split** — provenance vs extraction vs value quality
- [ ] **13.1 Page-level classification coverage**
- [ ] **13.2 Mixed-bundle summaries**
- [ ] **13.3 "What pages were searched" reporting**
- [ ] **14.1 Finer signature states** (present_clear, present_ambiguous, missing, stamp, printed_name, unreadable, etc.)
- [ ] **14.2 Signature states in field output**
- [ ] **15.1 Review mode separate from prompt mode**
- [ ] **15.2 Source preview side-by-side on correction**
- [ ] **15.3 List-first navigation before detail**
- [ ] **16.1 End-to-end test proving Red-Hat changes output**
- [ ] **16.2 End-to-end test proving Z3 changes output**
- [ ] **16.3 End-to-end test proving rerun changes output**

---

## Part 20: Summary

This plan addresses **every missing point** from prior work and the attachment:

1. **Execution model gaps** — LAYA, Z3, rerun, LLM grounding wired into pipeline (Phase 2)
2. **Code issues still in staging-v1** — provenance_confidence=1.0 fixed (Phase 1.1), field_state/routing_action for suspect fields fixed (Phase 1.2)
3. **Staging-v1 fixes that already exist** — verified in Phase 7 (review_summary, _empty_field, value_shape, etc.)
4. **JSON-stale issues** — verified in Phase 7 (new JSON should show correct values for not-found fields, etc.)
5. **Picture gaps** — all 9 covered in Phase 5
6. **Table gaps** — all 6 covered in Phase 3
7. **New parameter/schema gaps** — all 8 covered in Phase 4
8. **UI connections** — all 8 covered in Phase 6
9. **Verification** — all 6 covered in Phase 7
10. **Artifact integrity gates** — canonical snapshot, hash gate, export refusal, agreement check (Phase 8)
11. **Graph integrity / negative evidence / orphan handling** — negative evidence nodes, graph integrity summary, orphan detection, node-address consistency (Phase 9)
12. **Stable IDs and pointer stability** — stable document_id, stable node pointers, replay/export preserving anchors (Phase 10)
13. **First-class grounding propagation** — where grounding is created, how llm_extraction output becomes fields, serialization, UI consumption (Phase 11)
14. **Extraction-quality split** — clear separation of provenance, extraction, value quality (Phase 12)
15. **Mixed-bundle/page-level coverage** — page classification, bundle summary, pages searched (Phase 13)
16. **Signature taxonomy** — finer signature states (Phase 14)
17. **Review-mode vs prompt-mode separation** — separate modes, side-by-side preview, list-first navigation (Phase 15)
18. **End-to-end proof** — single tests proving Red-Hat, Z3, rerun change output and all artifacts agree (Phase 16)
19. **Key distinction** — wiring existing pieces vs inventing new ones (Phase 2.0)

**The plan now enforces truth, or refuses to export.** Not just "add more features."

**The plan now covers everything from the attachment:**
- Canonical artifact consistency gate ✓
- Graph integrity / negative evidence / orphan-node handling ✓
- Stable document_id and pointer stability ✓
- First-class grounding propagation ✓
- Separate extraction-quality signal ✓
- Mixed-bundle/page-level coverage ✓
- Signature taxonomy ✓
- Review-mode vs prompt-mode separation ✓
- End-to-end proof that Red-Hat actually changes output ✓

---

## Part 21: Mitigation Risk Plan

**These are SEVERE risks. Each has a specific mitigation plan.**

### M1: Vision Model Integration (Phase 5) — CRITICAL

**Risk:** `vision_service.py` has `NotImplementedError`. Need to implement actual vision model call. Costs money, adds latency, may not work for all images.

**Mitigation Plan:**

1. **Step 1: Verify vision model availability**
   - Check which vision models are available (GPT-4V, Claude Vision, Gemini Vision, etc.)
   - Check API access, costs, rate limits
   - Choose one model to start (recommend: cheapest/reliable option)

2. **Step 2: Implement minimal vision service**
   - Implement `_call_vision_model` for the chosen model
   - Test with a single image
   - Verify structured output is returned

3. **Step 3: Make vision analysis optional**
   - Add configuration flag: `VISION_ENABLED = False` by default
   - Only run vision analysis when explicitly enabled
   - This prevents accidental costs and latency

4. **Step 4: Run asynchronously**
   - Vision analysis should not block the main pipeline
   - Use async task queue (Celery, background job, etc.)
   - Return preliminary results immediately, add vision results when available

5. **Step 5: Cache results**
   - Cache vision analysis results by image hash
   - Avoid re-analyzing the same image

6. **Step 6: Progressive rollout**
   - Start with one document type (e.g., auto policy with accident photos)
   - Verify results are useful
   - Expand to other document types gradually

**Fallback:** If vision model is not available or too expensive, skip Phase 5 entirely. The system still works for text/PDF documents without picture analysis.

**Success Criteria:**
- Vision model is callable and returns structured results
- Vision analysis is optional and doesn't break the pipeline when disabled
- Vision analysis runs asynchronously without blocking the pipeline
- Vision results are cached to avoid re-analysis

---

### M2: LLM Grounding in Production (Phase 2.1) — CRITICAL

**Risk:** `completion=None` in production. Making it non-None means actual LLM calls in the hot path. Costs money, adds latency, may fail.

**Mitigation Plan:**

1. **Step 1: Locate the actual LLM call mechanism**
   - Find how the app makes LLM calls (look at `llm_extraction.py`, existing API calls, etc.)
   - Understand the model configuration, API keys, rate limits

2. **Step 2: Implement completion callable**
   - Create a `completion` function that wraps the actual LLM call
   - Test with a simple prompt
   - Verify structured output is returned

3. **Step 3: Make grounding optional**
   - Add configuration flag: `GROUNDING_ENABLED = False` by default
   - Only run grounding when explicitly enabled
   - This prevents accidental costs and latency

4. **Step 4: Run grounding selectively**
   - Only run grounding for fields that need it (missing fields, low-confidence fields)
   - Don't run grounding for every field
   - Use thresholding: only ground fields with confidence < threshold

5. **Step 5: Run asynchronously where possible**
   - For high-value documents, run grounding asynchronously
   - Return preliminary results immediately, add grounding when available

6. **Step 6: Handle failures gracefully**
   - If LLM call fails, fall back to label-pass results
   - Log the failure, don't crash the pipeline
   - Retry with exponential backoff (limited retries)

**Fallback:** If LLM grounding is too expensive or unreliable, keep `completion=None` and rely on label-pass extraction only. The system still works, just without grounding quotes.

**Success Criteria:**
- `completion` callable works and returns structured results
- Grounding is optional and doesn't break the pipeline when disabled
- Grounding runs selectively (not for every field)
- Failures are handled gracefully (fall back, don't crash)

---

### M3: Z3 Verification (Phase 2.3) — HIGH

**Risk:** Z3 may not exist, may be incomplete, may not work for all document types, may timeout.

**Mitigation Plan:**

1. **Step 1: Locate Z3 implementation**
   - Search for Z3 code in the codebase (`grep -rn "z3\|Z3" prompt_matrix/`)
   - Verify Z3 code exists and is functional
   - If Z3 doesn't exist, decide: implement from scratch or skip

2. **Step 2: Implement Z3 wiring**
   - Wire Z3 into the pipeline (Phase 2.3)
   - Test with a document that has compliance-bound fields
   - Verify Z3 produces pass/violation results

3. **Step 3: Make Z3 optional**
   - Add configuration flag: `Z3_ENABLED = False` by default
   - Only run Z3 when explicitly enabled

4. **Step 4: Handle timeouts**
   - Set timeout for Z3 verification (e.g., 30 seconds)
   - If Z3 times out, mark as "timed_out" and don't crash
   - Log the timeout, continue with pipeline

5. **Step 5: Run Z3 selectively**
   - Only run Z3 for compliance-bound fields (fields with `compliance_bound=True`)
   - Don't run Z3 for every field

**Fallback:** If Z3 doesn't exist or is too complex, skip Phase 2.3. The system still works without Z3 verification.

**Success Criteria:**
- Z3 implementation exists and is functional
- Z3 runs selectively (only for compliance fields)
- Z3 timeouts are handled gracefully
- Z3 results are reflected in output (pass/violation with counts)

---

### M4: Rerun Loop (Phase 2.4) — HIGH

**Risk:** Rerun loop may cause infinite loops or excessive resource usage. Helper functions need implementation.

**Mitigation Plan:**

1. **Step 1: Verify rerun infrastructure exists**
   - Check `rerun_stop_rule()`, `record_rerun()`, `replay_state()`, `reextract_for_type()` exist
   - Verify they are functional
   - If any don't exist, implement them

2. **Step 2: Implement helper functions**
   - Implement `next_approach(attempt)` — returns next re-extraction approach
   - Implement `reextract_low_confidence(fields, approach, completion, project_id)` — re-extracts fields
   - Test each helper function independently

3. **Step 3: Wire rerun loop with strict bounds**
   - Max attempts: 3
   - Max no-improvement rounds: 2
   - These bounds prevent infinite loops

4. **Step 4: Make rerun optional**
   - Add configuration flag: `RERUN_ENABLED = False` by default
   - Only run rerun when explicitly enabled

5. **Step 5: Run rerun asynchronously**
   - Rerun should not block the main pipeline
   - Use async task queue
   - Return preliminary results immediately, add rerun results when available

6. **Step 6: Handle failures gracefully**
   - If rerun fails, log the failure and continue with original results
   - Don't crash the pipeline

**Fallback:** If rerun infrastructure is incomplete or too complex, skip Phase 2.4. The system still works without rerun.

**Success Criteria:**
- Rerun loop runs with strict bounds (max 3 attempts, 2 no-improvement rounds)
- Rerun is optional and doesn't break the pipeline when disabled
- Rerun runs asynchronously without blocking the pipeline
- Rerun results are reflected in output (attempts > 0, history populated)
- Failures are handled gracefully

---

### M5: "Wire Existing Pieces" Assumption (Phase 2.0) — HIGH

**Risk:** Plan assumes LAYA, Z3, rerun infra, LLM grounding exist and are usable. If any don't exist or are incomplete, the plan needs major adjustment.

**Mitigation Plan:**

1. **Step 1: Audit each "existing piece"**
   - **LAYA/Red-Hat:** Check `founder_redhat.py`, `inquire_stream.py`, `redhat_routes.py`, `sandbox.py`. Verify functions exist and are callable.
   - **Z3:** Check for Z3 code. Verify it exists and is functional.
   - **Rerun:** Check `rerun_stop_rule()`, `record_rerun()`, `replay_state()`, `reextract_for_type()`. Verify they exist and are functional.
   - **LLM grounding:** Check `llm_extraction.py`, `completion` parameter usage. Verify LLM call mechanism exists.

2. **Step 2: Document what exists and what doesn't**
   - Create a checklist: "Exists? Functional? Wireable?"
   - For each piece that doesn't exist or isn't functional, flag it

3. **Step 3: Adjust plan based on audit results**
   - If a piece doesn't exist: add implementation to plan (or skip if too complex)
   - If a piece exists but isn't functional: add fix to plan
   - If a piece exists and is functional: proceed with wiring

4. **Step 4: Prioritize based on audit**
   - Pieces that exist and are functional: wire them in first
   - Pieces that don't exist: implement or skip based on priority

**Fallback:** If audit reveals major gaps, adjust the plan accordingly. Don't proceed with wiring if the pieces don't exist.

**Success Criteria:**
- Each "existing piece" is verified to exist and be functional
- Plan is adjusted based on audit results
- No assumption-driven failures

---

### M6: Costs — MEDIUM

**Risk:** LLM calls, vision calls, Z3, LAYA all cost money. Uncontrolled costs could be significant.

**Mitigation Plan:**

1. **Step 1: Budget for costs**
   - Estimate cost per LLM call, vision call, Z3 run, LAYA run
   - Estimate volume (documents per day/week/month)
   - Calculate total cost

2. **Step 2: Make expensive operations optional**
   - All expensive operations (LLM grounding, vision analysis, Z3, LAYA) are optional by default
   - Enable only when needed

3. **Step 3: Run selectively**
   - LLM grounding: only for missing/low-confidence fields
   - Vision analysis: only for documents with pictures, only when enabled
   - Z3: only for compliance-bound fields
   - LAYA: only for high-value documents or when enabled

4. **Step 4: Caching**
   - Cache LLM results by prompt hash
   - Cache vision results by image hash
   - Avoid re-calling for the same input

5. **Step 5: Monitor costs**
   - Track costs per operation
   - Set budget alerts
   - Review costs regularly

**Fallback:** If costs are too high, disable expensive operations and rely on label-pass extraction only.

**Success Criteria:**
- Costs are estimated and budgeted
- Expensive operations are optional by default
- Operations run selectively (not for everything)
- Caching avoids duplicate calls
- Costs are monitored

---

### M7: Performance — MEDIUM

**Risk:** Adding LLM grounding, vision analysis, Z3, rerun loop all add latency. Pipeline may become too slow.

**Mitigation Plan:**

1. **Step 1: Measure baseline performance**
   - Measure current pipeline latency (without any new features)
   - Establish baseline

2. **Step 2: Measure incremental latency**
   - Measure latency of each new feature individually
   - LLM grounding: X ms per field
   - Vision analysis: Y ms per image
   - Z3: Z ms per compliance field
   - Rerun: W ms per attempt

3. **Step 3: Run asynchronously**
   - All new features should run asynchronously where possible
   - Return preliminary results immediately
   - Add new results when available

4. **Step 4: Parallelize**
   - Run independent operations in parallel (e.g., Z3 and LAYA can run in parallel)
   - Don't serialize independent operations

5. **Step 5: Set timeouts**
   - Each operation has a timeout
   - If timeout exceeded, mark as timed out and continue
   - Don't let one slow operation block everything

6. **Step 6: Monitor performance**
   - Track pipeline latency with and without new features
   - Set performance budgets
   - Review regularly

**Fallback:** If performance is too slow, disable expensive operations or run them less frequently.

**Success Criteria:**
- Baseline performance is measured
- Incremental latency of each feature is measured
- Features run asynchronously and in parallel where possible
- Timeouts prevent one slow operation from blocking everything
- Performance is monitored and within budget

---

### M8: Reliability — MEDIUM

**Risk:** New code paths may introduce bugs or failures. Pipeline may become unreliable.

**Mitigation Plan:**

1. **Step 1: Comprehensive testing**
   - Test each new feature independently
   - Test combinations of features
   - Test with various document types
   - Test edge cases

2. **Step 2: Graceful degradation**
   - If LLM grounding fails, fall back to label-pass results
   - If vision analysis fails, fall back to no vision analysis
   - If Z3 fails, fall back to no Z3 verification
   - If rerun fails, fall back to original results
   - Never crash the pipeline due to one feature failure

3. **Step 3: Error handling**
   - All new code paths have proper error handling
   - Errors are logged (not just swallowed)
   - Errors don't crash the pipeline

4. **Step 4: Feature flags**
   - All new features are behind feature flags
   - Features can be turned off without code changes
   - If a feature causes issues, it can be disabled immediately

5. **Step 5: Progressive rollout**
   - Enable features for a small percentage of documents first
   - Monitor for issues
   - Gradually increase coverage

**Fallback:** If reliability is compromised, disable features via feature flags and fall back to original behavior.

**Success Criteria:**
- Each feature is tested independently and in combination
- Graceful degradation works (failures don't crash the pipeline)
- Error handling is proper (errors logged, not swallowed)
- Feature flags work (features can be turned off)
- Progressive rollout works (start small, expand gradually)

---

### M9: Testing Gaps — MEDIUM

**Risk:** End-to-end proof tests (Phase 16) may be difficult to set up. Test documents may not exist that trigger the required behaviors.

**Mitigation Plan:**

1. **Step 1: Identify test data needs**
   - Red-Hat test: need document where Red-Hat finds something
   - Z3 test: need document with compliance-bound fields where Z3 can verify
   - Rerun test: need document with low-confidence fields that can be improved

2. **Step 2: Find or create test documents**
   - Use existing test documents if they trigger the behaviors
   - Create synthetic test documents if needed (e.g., document with known compliance violations for Z3 test)
   - Document the test data requirements clearly

3. **Step 3: Implement tests incrementally**
   - Implement Red-Hat test first (Phase 16.1)
   - Implement Z3 test second (Phase 16.2)
   - Implement rerun test third (Phase 16.3)
   - Each test is independent and can be implemented separately

4. **Step 4: Fallback for missing test data**
   - If test data doesn't exist, create synthetic data
   - If synthetic data isn't possible, document the gap and skip the test

**Fallback:** If test data cannot be created, document the gap and skip the test. The plan can still proceed without end-to-end proof tests.

**Success Criteria:**
- Test data requirements are identified
- Test documents are found or created
- Tests are implemented incrementally
- Gaps are documented if test data cannot be created

---

### M10: JDF Structure Mismatch — LOW

**Risk:** JDF structure sketched in Part 9.5 may not match actual JDF implementation.

**Mitigation Plan:**

1. **Step 1: Verify JDF structure**
   - Examine actual JDF nodes in the codebase
   - Compare with sketched structure
   - Identify differences

2. **Step 2: Adjust sketched structure**
   - Update Part 9.5 to match actual JDF structure
   - If actual JDF is different, document the differences

3. **Step 3: Verify field-to-node mapping**
   - Verify that fields reference the correct JDF nodes
   - Test with documents that have tables, pictures, signatures

**Fallback:** If JDF structure is significantly different, update Part 9.5 to match. The plan can still proceed with the correct JDF structure.

**Success Criteria:**
- JDF structure is verified against actual implementation
- Part 9.5 matches actual JDF structure
- Field-to-node mapping is verified

---

## Summary of Mitigation Plans

| Risk | Severity | Mitigation Summary |
|------|----------|-------------------|
| **M1: Vision Model** | CRITICAL | Verify availability, implement minimally, make optional, run async, cache, progressive rollout |
| **M2: LLM Grounding** | CRITICAL | Locate LLM mechanism, implement completion, make optional, run selectively, handle failures gracefully |
| **M3: Z3 Verification** | HIGH | Locate Z3 code, verify functional, make optional, handle timeouts, run selectively |
| **M4: Rerun Loop** | HIGH | Verify infra exists, implement helpers, wire with strict bounds, make optional, run async, handle failures |
| **M5: "Wire Existing Pieces"** | HIGH | Audit each piece, document what exists/doesn't, adjust plan based on audit |
| **M6: Costs** | MEDIUM | Budget, make optional, run selectively, cache, monitor |
| **M7: Performance** | MEDIUM | Measure baseline, measure incremental, run async, parallelize, set timeouts, monitor |
| **M8: Reliability** | MEDIUM | Comprehensive testing, graceful degradation, error handling, feature flags, progressive rollout |
| **M9: Testing Gaps** | MEDIUM | Identify test data needs, find/create test documents, implement incrementally, document gaps |
| **M10: JDF Mismatch** | LOW | Verify JDF structure, adjust sketch, verify field-to-node mapping |

**Each mitigation plan includes:**
- Specific steps to execute
- Fallback options if the risk materializes
- Success criteria to verify the mitigation worked

**These are SEVERE risks. Each must be addressed before or during execution, not after.**
---






