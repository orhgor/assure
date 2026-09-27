# Assure / Parsure — Final Engineering Handoff

## Executive Summary

The latest run is a **diagnostic win**, not a product win.
It is now broad enough to show the real failure shape, but it is still not trustworthy for final output.

The system's remaining gap is no longer just OCR quality.
It is the **truth contract** between:
- source document
- JDF structure
- extracted field values
- field states and routing actions
- Red-Hat findings
- UI surfaces
- rerun / replay state
- export artifacts

The product must become a synchronized evidence system, not a flat parsing output.

---

## P0 — Critical Blockers

### Wrong Family Routing

**Observed:**
- Low-confidence documents are still being forced into a definite family.
- Weak keywords and weak extraction evidence can override uncertainty.

**Fix:**
- If confidence is below threshold, keep `uncertain`.
- Do not force reclassification on weak evidence.
- Require stronger evidence thresholds for family promotion.

**Hardening:**
- Keep `documents[].document_type` consistent with `classification.document_type`.
- Populate `matched_keywords` consistently.

---

### Semantic Misbinding

**Observed:**
- The system can mark garbage or nearby header text as accepted field values.
- Label anchoring is too naive.

**Fix:**
- Distinguish label vs value vs section header.
- Handle label/value on separate lines.
- Validate field output against expected type and shape.

**Hardening:**
- Reject section headers and address fragments for name fields.
- Use fallback extraction only after label-anchor validation fails.

---

### Evidence-State Confusion

**Observed:**
- `not_found`, `unverified`, `extraction_suspect`, and `accepted` are not separated cleanly enough.
- Not-found fields are still treated like review-required fields.

**Fix:**
- Add `field_state = not_found` as distinct from `unverified`.
- Not-found fields should not route to manual review by default.

**Hardening:**
- Use different routing actions for different states.
- Never treat "not found" as "needs review."

---

### Synthetic Confidence

**Observed:**
- `verification_confidence = 0.85` appears even when the field is not found.
- Provenance confidence can look perfect while the value is garbage.

**Fix:**
- For not-found fields, `verification_confidence` must be `null` or `0`.
- Split provenance from extraction/value quality.
- Add a separate `extraction_quality` or `value_quality` signal.

**Hardening:**
- Do not use provenance confidence as a surrogate for field correctness.
- Never accept a value solely because the source region is known.

---

### Graph Incompleteness

**Observed:**
- Missing fields are not represented with negative evidence nodes.
- Orphan / completeness reporting is not surfaced clearly.

**Fix:**
- Add negative evidence nodes for missing fields.
- Surface orphan/completeness reporting.

**Hardening:**
- A healthy graph must be measurable.
- Export refusal on graph integrity mismatch.

---

### Workflow Drift

**Observed:**
- List view, detail view, review mode, prompt mode, rerun mode, and export mode are still too mixed.

**Fix:**
- Separate modes: `review`, `prompt`, `upload`.
- Prompt answers must not mutate review state unless intentionally allowed.

**Hardening:**
- Keep review grounded in source evidence.
- Explicit mode gate.

---

### State Drift Across Artifacts

**Observed:**
- JSON, UI, PDF, replay, and runtime graph can disagree unless they derive from one canonical snapshot.

**Fix:**
- Canonical run snapshot.
- Artifact hash gate.
- Export refusal on mismatch.

**Hardening:**
- No silent state divergence across artifacts.

---

## P1 — High-Priority Issues

### Garbage Values with High Provenance Confidence

**Observed:**
- `policy_number.value` is garbage, but provenance confidence is 1.0.
- `insured_name.value` is clearly wrong, but provenance confidence is 1.0.

**Why it matters:**
Provenance confidence must not be read as correctness.

**Fix:**
- Split provenance from extraction/value quality.
- Add a separate `extraction_quality` or `value_quality` signal.
- If OCR is noisy, provenance can stay high while value quality drops.

**Hardening:**
- Do not use provenance confidence as a surrogate for field correctness.
- Never accept a value solely because the source region is known.

---

### Wrong Field Extracted for `insured_name`

**Observed:**
- Label anchor extraction grabbed an address section header / nearby noise instead of a name.

**Fix:**
- Distinguish label vs value vs section header.
- Handle label/value on separate lines.
- Validate field output against expected type and shape.

**Hardening:**
- Reject section headers and address fragments for name fields.
- Use fallback extraction only after label-anchor validation fails.

---

### Verification Confidence on Not-Found Fields

**Observed:**
- `effective_date.value = null`
- `verification_confidence = 0.85`

**Fix:**
- For not-found fields, `verification_confidence` must be `null` or `0`.
- Better: omit the field when there is nothing to verify.

**Hardening:**
- `field_state = not_found` must be distinct from `unverified`.
- Not-found fields should not receive `manual_review` by default.

---

### Low-Confidence Classification Forced to a Definite Type

**Observed:**
- `classification.confidence = 0.167`
- Output still forced toward `auto_policy`
- Detected path said `uncertain`

**Fix:**
- If confidence is below threshold, keep `uncertain`.
- Do not force reclassification on weak evidence.
- Require stronger evidence thresholds for family promotion.

**Hardening:**
- Keep `documents[].document_type` consistent with `classification.document_type`.
- Populate `matched_keywords` consistently.

---

### Low-Quality Extraction Still Runs and Returns Garbage

**Observed:**
- Page quality 0.28, low DPI / blur / low contrast.
- Extraction still returned invalid values.

**Fix:**
- Below threshold, either skip extraction or return `extraction_suspect` / null.
- Force human review for compliance-bound fields on low-quality pages.

**Hardening:**
- Add a page-quality threshold.
- Do not silently accept garbage from very poor scans.

---

### Signature Quality Is Too Vague

**Observed:**
- `signature_quality = questionable`

**Fix:**
Use more granular states:
- `present_clear`
- `present_ambiguous`
- `missing`
- `stamp`
- `printed_name`
- `unreadable`

**Hardening:**
- Ambiguous signatures must tell the reviewer what to check next.
- Do not collapse stamps and signatures.

---

### Number Quality Missed Garbage Policy Number

**Observed:**
- Garbage policy number not flagged.
- Quality report shows no invalid numbers.

**Fix:**
- Validate fields against expected patterns.
- Flag garbage text as `invalid_format`.

**Hardening:**
- Never mark non-matching garbage as valid.
- Validate policy numbers, VINs, dates, amounts, claim numbers explicitly.

---

### Classification Keywords Inconsistent with Basis

**Observed:**
- Basis says there was a keyword hit.
- `matched_keywords` is empty or inconsistent.

**Fix:**
- Populate `matched_keywords` from actual detection.
- Keep basis text aligned with the array.

**Hardening:**
- If keywords matched, list them.
- If none matched, say so explicitly.

---

## P2 — Medium-Priority Issues

### LLM Extraction Produced 0 Grounded Fields

**Observed:**
- Extraction ran, but nothing was grounded.

**Fix:**
- Require `grounding_quote`, `grounding_span`, and `source_span` for each extracted field.
- If grounding cannot be found, return `null`.

**Hardening:**
- No value without source grounding.
- If grounding fails, the output must say so.

---

### Review Summary Conflates Found/Review with Not-Found

**Observed:**
- Review summary counts all fields as review.
- Only a subset was actually found.

**Fix:**
- Add `field_state = not_found`.
- Separate found/review from not-found in the summary.

**Hardening:**
- Not-found should not route as manual review by default.
- Use different routing actions.

---

### `document_id` Is Null

**Observed:**
- Exported documents lack stable IDs.

**Fix:**
- Generate `document_id` at creation time.
- Persist it across exports and revisions.

**Hardening:**
- Never export a document without a stable ID.

---

### Mixed-Bundle Extraction Is Sparse

**Observed:**
- 54-page bundle with sparse extraction coverage.
- Relevant fields are missing from earlier pages.

**Fix:**
- Add page-level classification.
- Report which pages were searched.
- Extract by page family, not a single flat pass.

**Hardening:**
- Surface extraction coverage per page.
- Keep bundle-level and page-level summaries separate.

---

### Uncertain Document Has Empty Fields and No Reason Code

**Observed:**
- `document_type = uncertain`
- `fields = {}`

**Fix:**
Even for uncertain docs, include:
- What was detected.
- What keywords matched.
- What fields were searched for.
- Why it is uncertain.

**Hardening:**
- Empty output must be explainable.
- Do not let `uncertain` become a silent dead end.

---

## UI / Workflow Fixes

### Parsure UI Must Be List-First

- List first: what was parsed.
- Detail on click: one document at a time.
- Aggregate performance in a separate window/dashboard.

**Hardening:**
- Do not render all parsed documents as one flat body.
- Document status must be visible before detail.

---

### Modality-Specific Viewers

- Pictures.
- Signatures.
- Tables.

**Hardening:**
- Keep original resolution in review mode.
- Open the right viewer automatically by modality.

---

### Assure Source Preview on Correction

- Clicking an issue must show the original document.
- Source and parsed claim must be visible side by side.

**Hardening:**
- No correction without source context.
- Jump to the relevant page / region automatically.

---

### Separate Review Mode from Prompt Mode

- `review`
- `prompt`
- `upload`

**Hardening:**
- Prompt answers must not mutate review state unless intentionally allowed.
- Keep review grounded in source evidence.

---

### Synchronize JDF with Parsed Output

- Field selection ↔ JDF node selection.
- Missing fields ↔ negative evidence.
- Accepted fields ↔ explanation.

**Hardening:**
- No orphan parsed fields.
- No detached JDF nodes.

---

## Hidden Threats

### State Drift Across Artifacts

JSON, PDF, UI, replay, and the runtime graph can disagree.

**Mitigation:**
- Canonical run snapshot.
- Artifact hash gate.
- Export refusal on mismatch.

---

### Benchmark Overfitting / Corpus Drift

The system can optimize for the visible benchmark and still fail in the field.

**Mitigation:**
- Frozen benchmark set.
- Hidden holdout set.
- Family-stratified evaluation.
- Replay validation on live-like corpora.

---

### Semantic Over-Acceptance

Bad text can be accepted because it is near a label.

**Mitigation:**
- Require field/value compatibility, not just proximity.
- Reject section headers and label fragments.

---

### Synthetic Verification Confidence

A fixed-looking verification score conceals uncertainty.

**Mitigation:**
- Evidence-derived confidence only.
- No universal default value on not-found fields.

---

### Rerun Thrash

A post-Red-Hat rerun loop can spin indefinitely.

**Mitigation:**
- Versioned retry limit.
- Expected-gain threshold.
- Stop-on-no-improvement rule.
- Append-only rerun history.

---

### Prompt Contamination

Review mode and prompt mode can leak into each other.

**Mitigation:**
- Explicit mode gate.
- Prompt scope limited to current document.
- Prompt state cannot silently mutate review state.

---

## Prompt-Level Drift Controls

These are required guardrails, not the full solution.

### Grounded Extraction

- Every extracted value must include `grounding_quote`, `grounding_span`, and `source_span`.
- If grounding cannot be found, return `null`.

### Label vs Value Disambiguation

- Distinguish label, section header, and value.
- Reject header fragments for field values.

### Not-Found vs Unverified

- `not_found` is not `unverified`.
- Not-found fields must not route to manual review by default.

### Classification Thresholding

- If confidence is below threshold, keep `uncertain`.
- Do not force a family from weak evidence.

### Quality-Aware Extraction

- If page quality is very low, do not return garbage as if it were reliable.

### Number Validation

- Validate policy numbers, VINs, dates, amounts, claim numbers against patterns.
- Garbage must be `invalid_format`.

### Matched Keywords

- If keywords matched, list them.
- If none matched, say so explicitly.

---

## Backend / Data-Model Changes

### Backend Must Enforce

- No universal default verification confidence on not-found fields.
- Classification thresholding.
- Stable `document_id`.
- Graph-integrity summary.
- Review summary split into found/review vs not-found.
- Replay eligibility surfaced in the artifact.
- Canonical snapshot and artifact hash checks.

### Backend Must Support

- `field_state = not_found`
- `routing_action = field_not_found` or equivalent handled action
- `extraction_quality` / `value_quality`
- `invalid_format`
- `grounding_quote`
- `grounding_span`
- Signature granularity states

### Backend Must Not Assume

- The current buggy defaults are safe.
- Prompt changes alone will fix output quality.
- One routing action fits all states.

---

## Testing Requirements

The fixes are only real if they are tested.

### Required Tests

- Garbage OCR values become null or low confidence.
- Not-found fields do not get verification_confidence = 0.85.
- Low-confidence classification becomes uncertain.
- Invalid number formats are flagged.
- Grounding is required for extracted values.
- Fields in review are not conflated with fields not found.
- Document_id is stable across exports.
- Replay eligibility is visible and actionable.
- Mixed bundles report page-level coverage.
- Uncertain docs include reason codes.
- State drift between JSON/PDF/UI/replay is detected.

---

## Final Release Gate

Do not call the run ready unless all are true:
- Family routing passes benchmark.
- Semantic acceptance passes benchmark.
- Negative-evidence graphing is in place.
- Verification confidence is evidence-derived.
- Empty / uncertain docs have explicit reasons.
- UI is list-first and modality-aware.
- Assure preview shows the original document.
- Review mode is separated from prompt mode.
- JDF and parsed state synchronization is stable.
- Rerun controller is bounded and auditable.
- Canonical snapshot integrity gate passes.
- Hidden holdout still passes.

---

## Final Verdict

This is now the full-spectrum engineering handoff.

It is not a prompt.
It is not a visual cleanup.
It is a control-plane design that makes the system:
- Harder to fool.
- Easier to review.
- Safer to rerun.
- More truthful to export.
- More useful to the ICP.

The remaining gap is whether the system can reliably keep truth, evidence, and state aligned under real carrier-document conditions.

---

## Appendix: Code-Level Implementation Notes

> This appendix is a separate implementation reference for engineers.
> It is not part of the high-level handoff. Use it when implementing fixes.

### A. ExtractedField Dataclass — Add New Fields

**File:** `services/field_extractor.py`, lines 163-183

Add after `under_dispute`:

```python
# NEW: routing_action for directing next steps
routing_action: str = ""  # "manual_review", "field_not_found", "none", "accept"
# NEW: provenance confidence (separate from extraction confidence)
provenance_confidence: float = 0.0  # 0-1, how certain we are about the source span
# NEW: grounding information for LLM-extracted fields
grounding_quote: str | None = None  # exact source text that supports the value
grounding_span: dict[str, Any] | None = None  # char span of grounding text
```

**New valid states for `state`:**
- `"accepted"` — field verified, no review needed
- `"unverified"` — field found but needs review
- `"rejected"` — Z3 violation, compliance review required
- `"not_found"` — field not found in document (NEW)

---

### B. _build_extracted_field — Fix Verification Confidence and Add Routing

**File:** `services/field_extractor.py`, lines 789-855

**Change line 845:**

From:
```python
verification_confidence=DEFAULT_Z3_CONFIDENCE,
```

To:
```python
# Set verification_confidence based on whether field was found
if value is None and extraction_confidence == 0.0:
    verification_confidence = 0.0  # Not found → no verification possible
else:
    verification_confidence = DEFAULT_Z3_CONFIDENCE  # Found → use Z3 confidence
```

**Add provenance_confidence after extraction_confidence calculation (line 827):**

```python
# Provenance confidence: how certain are we about the source span?
if source_span is not None and extraction_confidence >= 0.5:
    provenance_confidence = min(1.0, extraction_confidence * 1.2)
else:
    provenance_confidence = extraction_confidence
```

**Add routing_action in the return statement (after line 853):**

```python
# Determine routing_action
if state == "accepted":
    routing_action = "accept"
elif state == "not_found":
    routing_action = "field_not_found"
elif state == "rejected":
    routing_action = "manual_review"
elif review_required:
    routing_action = "manual_review"
else:
    routing_action = "none"
```

---

### C. _apply_decision_policy — Add Not-Found Case

**File:** `services/field_extractor.py`, lines 857-894

**Change signature:**

From:
```python
def _apply_decision_policy(
    self,
    *,
    field_name: str,
    extraction_confidence: float,
    verification_confidence: float,
    has_z3_violation: bool,
    document_type: str,
) -> tuple[str, bool, str]:
```

To:
```python
def _apply_decision_policy(
    self,
    *,
    field_name: str,
    value: Any = None,  # NEW: pass value to check if found
    extraction_confidence: float,
    verification_confidence: float,
    has_z3_violation: bool,
    document_type: str,
) -> tuple[str, bool, str]:
```

**Add at start of method (before existing logic):**

```python
# Check if field was found
if value is None and extraction_confidence == 0.0:
    return ("not_found", False, "field not found in document")
```

**Update call site in _build_extracted_field (line 830):**

From:
```python
state, review_required, reason = self._apply_decision_policy(
    field_name=field_name,
    extraction_confidence=extraction_confidence,
    verification_confidence=DEFAULT_Z3_CONFIDENCE,
    has_z3_violation=has_z3_violation,
    document_type=document_type,
)
```

To:
```python
state, review_required, reason = self._apply_decision_policy(
    field_name=field_name,
    value=value,  # NEW
    extraction_confidence=extraction_confidence,
    verification_confidence=DEFAULT_Z3_CONFIDENCE,
    has_z3_violation=has_z3_violation,
    document_type=document_type,
)
```

---

### D. _assess_number_quality — Add Invalid Format and Not-Found

**File:** `services/field_extractor.py`, lines 1098-1119

**Change from:**
```python
def _assess_number_quality(self, value: Any, pattern: str | None) -> str:
    if isinstance(value, (int, float)):
        return "printed_good"
    if isinstance(value, str):
        if re.match(r"^[A-HJ-NPR-Z0-9]{17}$", value):
            return "printed_good"
        try:
            float(value)
            return "printed_good"
        except ValueError:
            return "clear"
    return "clear"
```

**To:**
```python
def _assess_number_quality(self, value: Any, pattern: str | None, field_name: str) -> str:
    """Assess number quality based on value type, pattern, and field type.

    Returns:
        "clear" - text field, no numeric pattern expected
        "printed_good" - numeric value looks valid
        "invalid_format" - value doesn't match expected pattern for field type
        "not_found" - value is None
    """
    if value is None:
        return "not_found"

    # Text fields don't need numeric validation
    text_fields = {"insured_name", "grantor_name", "grantee_name", "agent_name",
                   "owner_name", "borrower_name", "seller_name", "buyer_name",
                   "property_address", "policyholder", "lienholder_name"}
    if field_name in text_fields:
        return "clear"

    if isinstance(value, (int, float)):
        if isinstance(value, float) and value == int(value):
            return "printed_good"
        return "printed_good"

    if isinstance(value, str):
        try:
            float(value)
            return "printed_good"
        except ValueError:
            if field_name == "vehicle_vin":
                if re.match(r"^[A-HJ-NPR-Z0-9]{17}$", value):
                    return "printed_good"
                return "invalid_format"
            elif field_name in {"policy_number", "claim_number"}:
                if re.match(r"^[A-Z0-9]{6,20}$", value):
                    return "printed_good"
                return "invalid_format"
            elif field_name in {"effective_date", "expiration_date"}:
                if re.match(r"^\d{1,4}[-\s/]\d{1,2}[-\s/]\d{2,4}$", value):
                    return "printed_good"
                return "invalid_format"
            elif field_name in {"premium_annual", "payout_amount", "loan_amount",
                               "coverage_limit", "deductible", "interest_rate",
                               "consideration_amount"}:
                if re.match(r"^\$?\d{3,12}(\.\d{2})?$", value):
                    return "printed_good"
                return "invalid_format"
            else:
                return "clear"

    return "clear"
```

**Update call site:** Find where `_assess_number_quality` is called and add `field_name` parameter.

---

### E. extract_fields — Add Quality-Based Gating

**File:** `services/field_extractor.py`, lines 429-492

**Add after getting page_quality_score (around line 447):**

```python
# Quality-based extraction gating
MINIMUM_PAGE_QUALITY = 0.4

if page_quality_score < MINIMUM_PAGE_QUALITY:
    low_quality_mode = True
else:
    low_quality_mode = False
```

**In _build_extracted_field, handle low_quality_mode:**

After line 827 (extraction_confidence calculation):
```python
if low_quality_mode and extraction_confidence < 0.3:
    # Low quality page with low confidence extraction — be conservative
    if value is not None and isinstance(value, str) and len(value) < 3:
        # Likely garbage — return not_found
        value = None
        extraction_confidence = 0.0
        provenance_confidence = 0.0
```

**Add to quality_report:**

In the quality_report building section:
```python
if page_quality_score < MINIMUM_PAGE_QUALITY:
    quality_report["low_quality_extraction_warning"] = True
    quality_report["quality_threshold"] = MINIMUM_PAGE_QUALITY
    quality_report["actual_quality"] = page_quality_score
```

---

### F. v1_orchestrator.py — Update JSON Output

**File:** `services/v1_orchestrator.py`, `_build_output_json` method (lines 144-360)

**In the field block (around lines 280-320), add:**

```python
"routing_action": field.routing_action if field.routing_action else None,
"provenance_confidence": field.provenance_confidence,
"grounding_quote": field.grounding_quote,
"grounding_span": field.grounding_span,
```

---

### G. Tests to Add

Create `tests/test_field_extractor.py` with:

1. **Test ExtractedField defaults** — verify new fields exist with correct defaults
2. **Test not_found state** — verify state=not_found, review_required=False, verification_confidence=0.0
3. **Test decision policy not_found** — verify _apply_decision_policy returns ("not_found", False, "...") for value=None, confidence=0.0
4. **Test decision policy accepted** — verify returns ("accepted", False, "") for high confidence
5. **Test decision policy review_required** — verify returns ("unverified", True, "...") for low confidence
6. **Test decision policy rejected** — verify returns ("rejected", True, "...") for Z3 violation
7. **Test number quality valid** — verify "printed_good" for valid numbers
8. **Test number quality invalid** — verify "invalid_format" for garbage
9. **Test number quality text field** — verify "clear" for name fields
10. **Test number quality not_found** — verify "not_found" for None value
11. **Test quality-based gating** — verify low_quality_mode behavior

---

### H. Implementation Order for Fable

1. Read `field_extractor.py` completely
2. Modify ExtractedField dataclass (add new fields)
3. Update `_build_extracted_field` (fix verification_confidence, add routing, provenance)
4. Update `_apply_decision_policy` (add value param, not_found case)
5. Update `_assess_number_quality` (add invalid_format, not_found)
6. Add quality-based gating to `extract_fields`
7. Update `_build_output_json` in `v1_orchestrator.py`
8. Update signature quality assessment
9. Create `tests/test_field_extractor.py`
10. Run tests
11. Verify no regressions by running orchestrator on sample document
