"""Parsure field intelligence: no hallucination, VIN check digit, 3-rule policy,
field_state/routing_action separation, confidence_basis text, page-layout
helpers for both bundle shapes (spec §4, §5, §8 items 3, 6, 7)."""

from __future__ import annotations

import pytest

from prompt_matrix.services import field_extractor as fx

AUTO_POLICY = """AUTO POLICY DECLARATIONS
Policy Number: AP-2025-0001
Named Insured: John Q. Sample
Policy Period: 01/15/2025 to 01/15/2026
Vehicle: 2003 Honda Accord
VIN: 1HGCM82633A004352
Total Premium: $1,250.00
Liability Limit: $100,000
Collision Deductible: $500
Comprehensive Deductible: $250
Agent: Mary Agent
Insured's Signature: ______________
"""


def _extract(text: str = AUTO_POLICY, doc_type: str = "auto_policy", **kw):
    params = dict(parser_name="jdf-cli", parse_confidence=None, ocr_confidence=None, page_quality=[1.0])
    params.update(kw)
    return {f["name"]: f for f in fx.extract_fields(doc_type, [text], **params)}


def test_classification_is_capped_heuristic_with_uncertain_fallback():
    got = fx.classify_document(AUTO_POLICY)
    assert got["document_type"] == "auto_policy"
    assert 0 < got["confidence"] <= fx.CLASSIFICATION_CAP
    assert "keyword heuristic" in got["basis"]
    assert fx.classify_document("Dear diary, today was fine.")["document_type"] == "uncertain"
    assert fx.classify_document("")["document_type"] == "uncertain"


def test_missing_field_is_none_zero_review_not_a_guess():
    fields = _extract("Policy Number: X-1\nNamed Insured: A Person\n")
    missing = fields["vin"]
    assert missing["value"] is None and missing["raw"] is None
    assert missing["extraction_confidence"] == 0.0
    assert missing["review_required"] is True
    assert missing["reason"] == "field not found"
    assert missing["source_span"] is None and missing["provenance_confidence"] == 0.0
    fx.apply_decision_policy(missing)
    assert (missing["field_state"], missing["routing_action"]) == ("unverified", "manual_review")


def test_found_fields_carry_values_raw_and_text_range_spans():
    fields = _extract()
    assert fields["policy_number"]["value"] == "AP-2025-0001"
    assert fields["premium"]["value"] == 1250.0 and fields["premium"]["raw"] == "$1,250.00"
    assert fields["effective_date"]["value"] == "2025-01-15"
    assert fields["expiration_date"]["value"] == "2026-01-15"
    span = fields["vin"]["source_span"]
    assert span["page"] == 1 and span["span_type"] == "text_range"
    assert AUTO_POLICY[span["start_char"]:span["end_char"]] == "1HGCM82633A004352"
    assert fields["vin"]["provenance_confidence"] == 1.0


def test_blank_signature_line_is_not_a_signature():
    fields = _extract()
    sig = fields["signature"]
    assert sig["value"] is None
    assert sig["review_required"] is True
    assert sig["signature_quality"]["present"] in (False, None)


@pytest.mark.parametrize("vin,valid", [
    ("1HGCM82633A004352", True),
    ("1HGCM82633A004353", False),  # check digit off by one
    ("1HGCM8263A004352", False),  # 16 chars
    ("1HGCM82633A00435O", False),  # letter O
])
def test_vin_check_digit(vin, valid):
    assert fx.validate_vin(vin)["valid"] is valid


def test_invalid_vin_is_rejected_and_routed_to_compliance():
    fields = _extract(AUTO_POLICY.replace("1HGCM82633A004352", "1HGCM82633A004353"))
    vin = fields["vin"]
    assert vin["plausibility_violation"] is True and vin["verification_source"] == "vin_check"
    assert vin["z3_violation"] is False  # never reported as a Z3 hit
    fx.apply_decision_policy(vin)
    assert (vin["field_state"], vin["routing_action"]) == ("rejected", "compliance_review")


def test_confidence_basis_spells_out_the_product():
    fields = _extract(page_quality=[0.4])
    f = fields["policy_number"]
    assert f["extraction_confidence"] == pytest.approx(0.85 * 0.4, abs=0.01)
    assert "0.85" in f["confidence_basis"] and "0.40" in f["confidence_basis"]
    assert f["confidence_basis"].endswith(f"= {f['extraction_confidence']:.2f}")


def test_no_signal_gives_the_conservative_default():
    value, basis = fx.quality_weighted_confidence(parser_confidence=None, parser_name="unknown", page_quality=None)
    assert value == 0.5 and basis.startswith("no_signal_available")


def test_number_quality_penalty_reduces_confidence_and_flags_review():
    fields = _extract(ocr_confidence=0.4, parser_name="textract")
    premium = fields["premium"]
    assert premium["number_quality"]["quality"] == "faded"
    assert premium["review_required"] is True
    assert premium["extraction_confidence"] < 0.8 * 1.0
    assert "faded_penalty" in premium["confidence_basis"]


def test_three_rule_policy_and_vocabularies_never_cross():
    fields = _extract()
    rules = fx.coverage_plausibility(list(fields.values()))
    assert {r["rule"] for r in rules} == {
        "premium_positive", "premium_within_liability", "deductibles_in_range",
        "effective_before_expiration", "term_at_most_12_months", "vin_year_matches_vehicle",
    }
    assert all(r["passed"] for r in rules), rules
    fx.attach_plausibility(list(fields.values()), rules)
    for f in fields.values():
        fx.apply_decision_policy(f)
        assert f["field_state"] in fx.FIELD_STATES
        assert f["routing_action"] in fx.ROUTING_ACTIONS
        assert f["field_state"] not in fx.ROUTING_ACTIONS and f["routing_action"] not in fx.FIELD_STATES
    # Rule 1: verified (rule passed → 1.0), confident, not compliance-bound.
    coll = fields["collision_deductible"]
    assert coll["verification_confidence"] == 1.0
    assert (coll["field_state"], coll["routing_action"]) == ("accepted", "none")
    # Rule 2: compliance-bound never auto-accepts, even when verified.
    assert fields["premium"]["compliance_bound"] and fields["premium"]["verification_confidence"] == 1.0
    assert (fields["premium"]["field_state"], fields["premium"]["routing_action"]) == ("unverified", "manual_review")
    # No check applies → default 0.85 (spec item 10) and confident → accepted.
    agent = fields["agent_name"]
    assert agent["verification_confidence"] == fx.DEFAULT_VERIFICATION_CONFIDENCE
    assert agent["field_state"] == "accepted"


def test_failed_plausibility_rule_is_a_violation_but_not_z3():
    text = AUTO_POLICY.replace("Total Premium: $1,250.00", "Total Premium: $50,000.00")
    fields = _extract(text)
    rules = fx.coverage_plausibility(list(fields.values()))
    bad = [r for r in rules if r["rule"] == "premium_within_liability"][0]
    assert bad["passed"] is False
    fx.attach_plausibility(list(fields.values()), rules)
    premium = fields["premium"]
    assert premium["plausibility_violation"] is True and premium["z3_violation"] is False
    assert premium["verification_source"] == "plausibility_rule" and premium["verification_confidence"] == 0.0
    fx.apply_decision_policy(premium)
    assert (premium["field_state"], premium["routing_action"]) == ("rejected", "compliance_review")


def test_real_z3_violation_maps_by_node_id_and_applies_penalty():
    bundle = {"jdf": {"$jdf": "1.0", "meta": {}, "pages": [{"id": "page-1", "pageSize": {"width": 210, "height": 297},
              "elements": [{"id": "el-1", "type": "text", "content": "Total Premium: $1,250.00", "position": {"x": 20, "y": 30}, "width": 60, "height": 5}]}]}}
    layout = fx.page_layout(bundle)
    fields = {f["name"]: f for f in fx.extract_fields("auto_policy", fx.page_texts(bundle), layout=layout, parser_name="jdf-cli",
                                                      parse_confidence=None, ocr_confidence=None, page_quality=[1.0])}
    premium = fields["premium"]
    assert premium["field_source_node_id"] == "el-1"
    assert premium["source_span"]["span_type"] == "bbox_relative"
    assert premium["source_span"]["bbox"] == [pytest.approx(20 / 210, abs=1e-3), pytest.approx(30 / 297, abs=1e-3),
                                              pytest.approx(80 / 210, abs=1e-3), pytest.approx(35 / 297, abs=1e-3)]
    before = premium["extraction_confidence"]
    fx.attach_z3_violations(list(fields.values()), [{"severity": "high", "category": "Z3 Contradiction", "description": "premium contradicts ledger", "node_id": "el-1"}])
    assert premium["z3_violation"] is True and premium["verification_source"] == "z3"
    assert premium["extraction_confidence"] == pytest.approx(before * 0.9, abs=0.01)
    assert "z3_penalty (0.90)" in premium["confidence_basis"]
    fx.apply_decision_policy(premium)
    assert (premium["field_state"], premium["routing_action"]) == ("rejected", "compliance_review")


def test_page_texts_handles_jdf_cli_tree_chunks_and_flat_text():
    jdf_cli = {"jdf": {"pages": [
        {"id": "p1", "elements": [{"type": "text", "content": "Page one"}, {"type": "image", "ocr": {"blocks": [{"text": "OCR words", "confidence": 0.9}]}}]},
        {"id": "p2", "elements": [{"type": "text", "text": "Page two"}]},
    ]}}
    assert fx.page_texts(jdf_cli) == ["Page one\nOCR words", "Page two"]
    tree = {"jdf": {"document_id": "d", "meta": {}, "body": [
        {"type": "section", "id": "sec-1", "title": "Page 1", "meta": {"source_page": 1},
         "children": [{"type": "paragraph", "id": "p-1", "content": "Policy Number: Z-9"}]},
        {"type": "section", "id": "sec-2", "title": "Page 2", "meta": {"source_page": 2}, "children": [{"type": "paragraph", "id": "p-2", "content": "second"}]},
    ]}}
    assert fx.page_texts(tree) == ["Policy Number: Z-9", "second"]
    assert fx.page_layout(tree)[0][0]["node_id"] == "p-1"
    chunks = {"jdf": None, "chunks": [{"id": "c1", "text": "a", "page": 1}, {"id": "c2", "text": "b", "page": 2}], "page_count": 2}
    assert fx.page_texts(chunks) == ["a", "b"]
    assert fx.page_texts({"text": "one\ftwo"}) == ["one", "two"]


def test_cross_document_conflicts_only_on_differing_values():
    r1 = {"report_id": "a", "document_id": "d1", "fields": [{"name": "policy_number", "value": "AP-1"}, {"name": "vin", "value": "1HGCM82633A004352"}]}
    r2 = {"report_id": "b", "document_id": "d2", "fields": [{"name": "policy_number", "value": "ap 1"}, {"name": "vin", "value": "4T1BF3EK6BU123456"}]}
    r3 = {"report_id": "c", "document_id": "d3", "fields": [{"name": "insured_name", "value": None}]}
    conflicts = fx.cross_document_conflicts([r1, r2, r3])
    assert [c["field"] for c in conflicts] == ["vin"]
    assert conflicts[0]["kind"] == "cross_document" and len(conflicts[0]["values"]) == 2


def test_taxonomy_covers_every_icp_type_with_compliance_flags():
    for doc_type in fx.DOCUMENT_TYPES:
        specs = fx.FIELD_TAXONOMY[doc_type]
        assert 6 <= len(specs) <= 12, doc_type
        assert all(s.field_type in fx.FIELD_TYPES for s in specs)
        assert any(s.field_type == "signature" and s.compliance_bound for s in specs)
    auto = {s.name: s for s in fx.FIELD_TAXONOMY["auto_policy"]}
    assert all(auto[n].compliance_bound for n in ("policy_number", "vin", "premium", "liability_limit", "signature"))
    assert not auto["agent_name"].compliance_bound
