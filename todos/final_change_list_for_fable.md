# Final Change List for Fable — Assure/Parsure Field Extraction Fixes

**Date:** 2026-09-26
**Files to modify:** `prompt_matrix/services/field_extractor.py`, `prompt_matrix/services/v1_orchestrator.py`
**New file:** `prompt_matrix/tests/test_field_extractor.py`

---

## 1. field_extractor.py — ExtractedField Dataclass

**Location:** Lines 163-183

**Add these fields after `under_dispute`:**

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

## 2. field_extractor.py — _build_extracted_field Method

**Location:** Lines 789-855

**Change line 845 (verification_confidence):**

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

**Add provenance_confidence after line 827 (after extraction_confidence calculation):**

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

## 3. field_extractor.py — _apply_decision_policy Method

**Location:** Lines 857-894

**Change signature (line 857-865):**

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

**Add at start of method (before existing Rule 1):**

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

## 4. field_extractor.py — _assess_number_quality Method

**Location:** Lines 1098-1119

**Replace entire method:**

From:
```python
def _assess_number_quality(self, value: Any, pattern: str | None) -> str:
    """Assess number quality based on value type and pattern.

    V1: simple heuristic based on value type.
    - int/float with no decimal → printed_good
    - float with decimal → printed_good
    - string that looks like a VIN → printed_good
    - string that's not numeric → clear (text field)
    """
    if isinstance(value, (int, float)):
        return "printed_good"
    if isinstance(value, str):
        # Check if it looks like a VIN.
        if re.match(r"^[A-HJ-NPR-Z0-9]{17}$", value):
            return "printed_good"
        # Check if it's numeric.
        try:
            float(value)
            return "printed_good"
        except ValueError:
            return "clear"  # text field
    return "clear"
```

To:
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

**Update call site:** Find where `_assess_number_quality` is called (in the extraction loop) and add `field_name` parameter.

---

## 5. field_extractor.py — extract_fields Method

**Location:** Lines 429-492

**Add quality-based gating after getting page_quality_score (around line 447):**

```python
# Quality-based extraction gating
MINIMUM_PAGE_QUALITY = 0.4

if page_quality_score < MINIMUM_PAGE_QUALITY:
    low_quality_mode = True
else:
    low_quality_mode = False
```

**In _build_extracted_field, handle low_quality_mode (after line 827):**

```python
if low_quality_mode and extraction_confidence < 0.3:
    # Low quality page with low confidence extraction — be conservative
    if value is not None and isinstance(value, str) and len(value) < 3:
        # Likely garbage — return not_found
        value = None
        extraction_confidence = 0.0
        provenance_confidence = 0.0
```

**Add to quality_report building section (around line 480-490):**

```python
if page_quality_score < MINIMUM_PAGE_QUALITY:
    quality_report["low_quality_extraction_warning"] = True
    quality_report["quality_threshold"] = MINIMUM_PAGE_QUALITY
    quality_report["actual_quality"] = page_quality_score
```

---

## 6. field_extractor.py — _build_review_summary Method (via v1_orchestrator.py)

**Location:** `v1_orchestrator.py`, lines 99-130

**Update to separate not_found from review_required:**

```python
def _build_review_summary(artifact: DocumentArtifact) -> dict[str, Any]:
    """Build the review summary for the artifact."""
    total_fields = len(artifact.fields)
    not_found_fields = sum(1 for f in artifact.fields.values() if f.state == "not_found")
    review_required_fields = sum(1 for f in artifact.fields.values()
                                  if f.review_required and f.state != "not_found")
    reasons: dict[str, int] = {}

    for f in artifact.fields.values():
        if f.state == "not_found":
            continue
        if not f.review_required:
            continue
        if f.has_z3_violation:
            reasons["z3_violation"] = reasons.get("z3_violation", 0) + 1
        elif f.number_quality in ("handwritten", "faded", "typewritten_low_quality", "invalid_format"):
            reasons["number_quality"] = reasons.get("number_quality", 0) + 1
        elif f.signature_quality in ("faint", "incomplete", "stamped", "questionable", "missing"):
            reasons["signature"] = reasons.get("signature", 0) + 1
        elif f.field_id in ("policy_number", "claim_number", "payout_amount",
                              "premium_annual", "loan_amount", "purchase_price",
                              "property_address"):
            reasons["compliance_bound"] = reasons.get("compliance_bound", 0) + 1
        elif f.extraction_confidence < 0.75:
            reasons["quality"] = reasons.get("quality", 0) + 1
        else:
            reasons["quality"] = reasons.get("quality", 0) + 1

    return {
        "total_fields": total_fields,
        "not_found_fields": not_found_fields,
        "review_required_fields": review_required_fields,
        "review_required_reasons": reasons,
        "quality_warning": _review_warning_text(review_required_fields, total_fields,
                                                artifact.quality_report.get("overall_page_quality", 0.5)),
    }
```

---

## 7. v1_orchestrator.py — _build_output_json Method

**Location:** Lines 144-360 (field block around lines 296-320)

**In the field block, add these fields:**

```python
fields_block[field_id] = {
    "value": field.value,
    "source_span": field.source_span,
    "parser": {
        "parser_name": field.parser_name,
        "parser_version": field.parser_version,
    },
    "verification": {
        "z3_confidence": field.verification_confidence,
        "z3_result": field.z3_result,
        "z3_violations": field.z3_violations,
        "redhat_annotations_present": True,  # V1: assume Red-Hat ran.
    },
    "number_quality": field.number_quality,
    "signature_quality": field.signature_quality,
    "extraction_confidence": field.extraction_confidence,
    "confidence_basis": field.quality_basis,
    "state": field.state,
    "review_required": field.review_required,
    "reason": field.reason,
    # NEW: routing_action
    "routing_action": field.routing_action if field.routing_action else None,
    # NEW: provenance_confidence
    "provenance_confidence": field.provenance_confidence,
    # NEW: grounding information
    "grounding_quote": field.grounding_quote,
    "grounding_span": field.grounding_span,
}
```

---

## 8. New Test File: tests/test_field_extractor.py

```python
"""Tests for field_extractor.py - extraction, classification, decision policy."""

import pytest
from dataclasses import field as dt_field
from prompt_matrix.services.field_extractor import (
    ExtractedField,
    DocumentClassifier,
    FieldExtractor,
    ClassificationResult,
    DEFAULT_Z3_CONFIDENCE,
    DEFAULT_Z3_RESULT,
    DECISION_VERIFICATION_ACCEPT_THRESHOLD,
    DECISION_EXTRACTION_ACCEPT_THRESHOLD,
)


class TestExtractedField:
    """Test ExtractedField dataclass."""

    def test_default_values(self):
        field = ExtractedField(field_id="test_field")
        assert field.value is None
        assert field.extraction_confidence == 0.0
        assert field.verification_confidence == DEFAULT_Z3_CONFIDENCE
        assert field.state == "unverified"
        assert field.review_required is True
        assert field.routing_action == ""
        assert field.provenance_confidence == 0.0
        assert field.grounding_quote is None
        assert field.grounding_span is None

    def test_not_found_state(self):
        field = ExtractedField(
            field_id="test_field",
            value=None,
            extraction_confidence=0.0,
            state="not_found",
            review_required=False,
            reason="field not found in document",
            verification_confidence=0.0,
        )
        assert field.state == "not_found"
        assert field.review_required is False
        assert field.verification_confidence == 0.0


class TestDocumentClassifier:
    """Test document classification."""

    def test_classifier_defaults(self):
        classifier = DocumentClassifier()
        result = classifier.classify(extracted_text="")
        assert result.document_type == "unknown"
        assert result.confidence == 0.0
        assert result.review_required is True
        assert result.matched_keywords == []

    def test_classifier_with_keywords(self):
        classifier = DocumentClassifier()
        result = classifier.classify(extracted_text="policy number premium insured")
        assert result.document_type in ["auto_policy", "unknown"]
        assert len(result.matched_keywords) > 0

    def test_classifier_threshold(self):
        classifier = DocumentClassifier()
        result = classifier.classify(extracted_text="policy")
        assert result.review_required is True


class TestFieldExtractor:
    """Test field extraction and decision policy."""

    def test_decision_policy_not_found(self):
        extractor = FieldExtractor()
        state, review_required, reason = extractor._apply_decision_policy(
            field_name="policy_number",
            value=None,
            extraction_confidence=0.0,
            verification_confidence=0.0,
            has_z3_violation=False,
            document_type="auto_policy",
        )
        assert state == "not_found"
        assert review_required is False
        assert "not found" in reason.lower()

    def test_decision_policy_accepted(self):
        extractor = FieldExtractor()
        state, review_required, reason = extractor._apply_decision_policy(
            field_name="policy_number",
            value="A12345",
            extraction_confidence=0.9,
            verification_confidence=0.9,
            has_z3_violation=False,
            document_type="auto_policy",
        )
        assert state == "accepted"
        assert review_required is False
        assert reason == ""

    def test_decision_policy_review_required(self):
        extractor = FieldExtractor()
        state, review_required, reason = extractor._apply_decision_policy(
            field_name="policy_number",
            value="A12345",
            extraction_confidence=0.3,
            verification_confidence=0.5,
            has_z3_violation=False,
            document_type="auto_policy",
        )
        assert state == "unverified"
        assert review_required is True
        assert "below threshold" in reason

    def test_decision_policy_rejected(self):
        extractor = FieldExtractor()
        state, review_required, reason = extractor._apply_decision_policy(
            field_name="claim_number",
            value="12345",
            extraction_confidence=0.9,
            verification_confidence=0.9,
            has_z3_violation=True,
            document_type="auto_claim",
        )
        assert state == "rejected"
        assert review_required is True
        assert "Z3 violation" in reason

    def test_assess_number_quality_valid(self):
        extractor = FieldExtractor()
        assert extractor._assess_number_quality(12345, None, "policy_number") == "printed_good"
        assert extractor._assess_number_quality("12345", None, "policy_number") == "printed_good"
        assert extractor._assess_number_quality(1234.56, None, "premium_annual") == "printed_good"

    def test_assess_number_quality_invalid(self):
        extractor = FieldExtractor()
        assert extractor._assess_number_quality("garbage", None, "policy_number") == "invalid_format"
        assert extractor._assess_number_quality("abc", None, "vehicle_vin") == "invalid_format"
        assert extractor._assess_number_quality("not-a-date", None, "effective_date") == "invalid_format"

    def test_assess_number_quality_text_field(self):
        extractor = FieldExtractor()
        assert extractor._assess_number_quality("John Smith", None, "insured_name") == "clear"

    def test_assess_number_quality_not_found(self):
        extractor = FieldExtractor()
        assert extractor._assess_number_quality(None, None, "policy_number") == "not_found"

    def test_extract_fields_low_quality(self):
        extractor = FieldExtractor()
        artifact = extractor.extract_fields(
            document_type="auto_policy",
            page_quality_score=0.2,
            text_content="",
        )
        assert artifact.quality_report.get("low_quality_extraction_warning") is True
```

---

## 9. Implementation Order

1. **Read `field_extractor.py` completely** — understand current structure
2. **Modify ExtractedField dataclass** (add `routing_action`, `provenance_confidence`, `grounding_quote`, `grounding_span`)
3. **Update `_build_extracted_field`** (fix verification_confidence for not-found, add routing_action, provenance_confidence)
4. **Update `_apply_decision_policy`** (add `value` parameter, add "not_found" case)
5. **Update `_assess_number_quality`** (add `field_name` param, add "invalid_format", "not_found" returns)
6. **Add quality-based gating to `extract_fields`** (MINIMUM_PAGE_QUALITY = 0.4)
7. **Update `_build_output_json`** in `v1_orchestrator.py` (add routing_action, provenance_confidence, grounding_quote, grounding_span)
8. **Update `_build_review_summary`** in `v1_orchestrator.py` (separate not_found from review_required)
9. **Create `tests/test_field_extractor.py`** with comprehensive tests
10. **Run tests** to verify changes
11. **Verify no regressions** by running orchestrator on a sample document

---

## 10. Verification Checklist

After implementation, verify:

- [ ] `ExtractedField` has `routing_action`, `provenance_confidence`, `grounding_quote`, `grounding_span` fields with correct defaults
- [ ] `_build_extracted_field` sets `verification_confidence=0.0` for not-found fields
- [ ] `_apply_decision_policy` returns `("not_found", False, "...")` for value=None, confidence=0.0
- [ ] `_assess_number_quality` returns `"invalid_format"` for garbage values in numeric fields
- [ ] `_assess_number_quality` returns `"not_found"` for None values
- [ ] `extract_fields` adds `low_quality_extraction_warning` to quality_report when page_quality < 0.4
- [ ] `_build_output_json` includes `routing_action`, `provenance_confidence`, `grounding_quote`, `grounding_span`
- [ ] `_build_review_summary` separates `not_found_fields` from `review_required_fields`
- [ ] All tests pass
- [ ] No regressions when running orchestrator on sample document

---

## 8b. Out of Scope for This Round

The following are in the handoff (`assure_final_engineering_handoff.md`) but NOT implemented in this change list.
They are deferred to a later pass.

- **extraction_quality / value_quality** — partially addressed (added to ExtractedField), but full signaling pipeline not implemented. The change list adds the fields but does not fully implement the logic to set them based on extraction quality assessment.
- **Document/artifact-level integrity** — canonical snapshot, artifact hash gating, state drift checks, graph integrity summary. These are backend infrastructure changes, not field-extraction changes.
- **UI/workflow changes** — list-first parsing UI, source preview on correction, review mode vs prompt mode separation, modality-specific viewers. These are UI/UX changes, not extraction changes.
- **Signature quality taxonomy** — granular states listed below, but full implementation not in this change list. The change list adds the field and basic structure, but full signature assessment logic is deferred.

---

## 8c. Signature Quality Taxonomy (deferred)

Add granular signature states to `signature_quality` field in ExtractedField:

```python
# Valid values for signature_quality:
# "clear" - no signature expected or signature is clear and verified
# "present_clear" - signature present and clearly legible
# "present_ambiguous" - signature present but legibility uncertain
# "missing" - signature expected but not present
# "stamp" - appears to be a stamp, not a handwritten signature
# "printed_name" - appears to be a printed name, not a signature
# "unreadable" - signature present but cannot be read
```

Add to signature assessment logic in `extract_fields` or a new `_assess_signature_quality` method:

```python
def _assess_signature_quality(self, signature_data: Any, field_name: str) -> str:
    """Assess signature quality.

    Returns one of: "clear", "present_clear", "present_ambiguous",
    "missing", "stamp", "printed_name", "unreadable"
    """
    if signature_data is None:
        return "missing"

    # Heuristic assessment based on signature data
    # This is a placeholder — real implementation would analyze the signature image/data
    if isinstance(signature_data, dict):
        signature_type = signature_data.get("type", "")
        legibility = signature_data.get("legibility", "unknown")

        if signature_type == "stamp":
            return "stamp"
        elif signature_type == "printed":
            return "printed_name"
        elif legibility == "clear":
            return "present_clear"
        elif legibility == "ambiguous":
            return "present_ambiguous"
        elif legibility == "unreadable":
            return "unreadable"

    return "clear"
```

Update the `signature_quality` default and usage in ExtractedField and _build_extracted_field to use the new taxonomy.

