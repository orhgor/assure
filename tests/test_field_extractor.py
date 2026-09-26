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
    short = "Policy Number: X-1\nNamed Insured: A Person\n"
    fields = _extract(short)
    missing = fields["vin"]
    assert missing["value"] is None and missing["raw"] is None
    assert missing["extraction_confidence"] == 0.0
    assert missing["review_required"] is True
    assert missing["provenance_confidence"] == 0.0
    # 40 characters of text is not a readable page: its silence about the VIN
    # is "could not be read", not "absent" (fx.READABLE_MIN_CHARS = 200).
    assert missing["evidence_state"] == "unreadable" and missing["reason"] == "Page could not be read"
    assert missing["source_span"] == {"span_type": "absent", "pages": [1]}
    assert missing["evidence"]["kind"] == "absent" and missing["evidence"]["searched_pages"] == [1]
    assert missing["evidence"]["searched_chars"] == len(short.strip()) and missing["evidence"]["anchor_node_id"] is None  # flat text: no node ids to anchor to
    fx.apply_decision_policy(missing)
    assert (missing["field_state"], missing["routing_action"]) == ("unverified", "manual_review")
    assert missing["evidence_state"] == "unreadable"  # the policy does not overwrite an absent field's state
    # On a readable page (≥ 200 chars, quality ≥ 0.5) the same silence is a fact about the document.
    long_text = short + "Coverage notes. " * 20
    absent = _extract(long_text)["vin"]
    assert absent["evidence_state"] == "not_on_document" and absent["reason"] == "Not on this document type"
    assert absent["confidence_basis"].startswith("not found on 1 page (")
    low = _extract(long_text, page_quality=[0.28])["vin"]
    assert low["evidence_state"] == "unreadable" and low["reason"] == "Page could not be read"  # quality < 0.3
    mid = _extract(long_text, page_quality=[0.4])["vin"]
    assert mid["evidence_state"] == "not_on_document" and "page quality low on p.1" in mid["confidence_basis"]


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
        assert 6 <= len(specs) <= 13, doc_type  # CMS-1500 carries 13 (12 boxes + signature)
        assert fx.TYPE_FAMILY[doc_type] in fx.DOCUMENT_FAMILIES
        assert all(s.field_type in fx.FIELD_TYPES for s in specs)
        assert any(s.field_type == "signature" and s.compliance_bound for s in specs)
    auto = {s.name: s for s in fx.FIELD_TAXONOMY["auto_policy"]}
    assert all(auto[n].compliance_bound for n in ("policy_number", "vin", "premium", "liability_limit", "signature"))
    assert not auto["agent_name"].compliance_bound


# --------------------------------------------------------------------------
# Classification by evidence (2026-09-26): keywords are a hint, found fields decide
# --------------------------------------------------------------------------

#: A lender-facing real-estate declarations page whose exclusions mention the
#: auto-claim vocabulary. Keywords alone: auto_claim 8, auto_policy 6,
#: property_policy 6 (no near-tie, auto_claim confidence 8/12 = 0.667). The
#: label pass: property_policy 10/11 fields, auto_policy 6/12, auto_claim 1/10.
REAL_ESTATE_LINES = [
    "SCHEDULE OF COVERAGE",
    "Policy Number: RE-500697",
    "Named Insured: Rosa and Miguel Alvarez",
    "Premises: 42 Sandwich Road, Plymouth, MA 02360",
    "Effective Date: 05/01/2025",
    "Expiration Date: 05/01/2026",
    "Dwelling: $425,000",
    "Personal Property Coverage: $212,500",
    "Annual Premium: $2,140.00",
    "All Peril Deductible: $2,500",
    "Agent: Elliot Marsh",
    "EXCLUSIONS. This policy does not cover any vehicle or its VIN, nor collision or accident damage to a vehicle or the repair of one.",
    "Open a claim number with your auto carrier and report the date of loss to them, not to this agent.",
]
#: The same page titled so that property_policy lands one keyword behind
#: auto_claim (7 vs 8): the near-tie rule decides by the label pass.
REAL_ESTATE_NEAR_TIE_LINES = ["REAL ESTATE POLICY"] + REAL_ESTATE_LINES[1:]


def test_real_estate_wording_classifies_as_property_policy():
    text = ("Hazard insurance for the real estate at 42 Sandwich Road; mortgagee: First Plymouth Bank. "
            "Property insurance premium and dwelling coverage as stated in the schedule.")
    cls = fx.classify_document(text)
    assert cls["document_type"] == "property_policy"
    assert {"real estate", "hazard insurance", "mortgagee", "property insurance", "dwelling coverage"} <= set(cls["matched_keywords"])


def test_keywords_alone_mistype_the_real_estate_page_as_auto_claim():
    """The customer's case, by construction: the vocabulary of the exclusions
    outnumbers the vocabulary of the declarations. Recorded here so the
    orchestrator test (tests/test_v1_orchestrator) proves the fix from the
    same input."""
    text = "\n".join(REAL_ESTATE_LINES)
    cls = fx.classify_document(text)
    assert cls["document_type"] == "auto_claim" and cls["confidence"] < 0.7
    counts = fx.found_field_counts([text])
    assert counts["property_policy"] == 10 and counts["auto_claim"] == 1 and counts["auto_policy"] == 6
    assert list(counts) == list(fx.FIELD_TAXONOMY)


def test_keyword_near_tie_is_decided_by_the_label_pass():
    text = "\n".join(REAL_ESTATE_NEAR_TIE_LINES)
    cls = fx.classify_document(text)
    assert cls["document_type"] == "property_policy"
    assert cls["basis"].startswith("keyword near-tie (auto_claim 8, property_policy 7) decided by the label pass: property_policy 10/11 fields found vs auto_claim 1/10")
    assert 0 < cls["confidence"] <= fx.CLASSIFICATION_CAP
    # An exact tie that the label pass cannot break stays uncertain.
    tie = fx.classify_document("grantor grantee conveys — borrower lender escrow")
    assert tie["document_type"] == "uncertain" and "label pass found the same number of fields for both" in tie["basis"]


def test_count_found_fields_excludes_signature_and_handles_unknown_type():
    text = "\n".join(REAL_ESTATE_LINES) + "\nAuthorized Signature: /s/ Elliot Marsh"
    assert fx.count_found_fields("property_policy", [text]) == 10  # signature not counted
    assert fx.count_found_fields("uncertain", [text]) == 0
    assert fx.count_found_fields("property_policy", []) == 0
    assert fx.count_found_fields("property_policy", ["", None]) == 0


def test_field_needs_review_is_the_one_rule():
    assert fx.field_needs_review({"routing_action": "manual_review", "field_state": "unverified"}) is True
    assert fx.field_needs_review({"routing_action": "none", "field_state": "accepted"}) is False
    assert fx.field_needs_review({"routing_action": "none", "field_state": "rejected"}) is True  # adjudicator rejection, no routing
    assert fx.field_needs_review({"routing_action": "none", "field_state": "disputed"}) is True
    assert fx.field_needs_review({}) is False  # no routing, no state: nothing asked
    assert fx.field_needs_review({"value": None, "routing_action": "none", "field_state": "unverified"}) is False  # not-found alone is not the rule; the policy routes it


# --------------------------------------------------------------------------
# Document family gate (2026-09-26): a CMS-1500 medical claim read as auto_policy
# --------------------------------------------------------------------------

#: A CMS-1500 (NUCC 02/12) health insurance claim form as its text layer reads.
#: Box 10b literally asks "Auto Accident?" — the only auto_claim keyword on the
#: page, and the one that started the customer's mis-typing.
CMS_1500_LINES = [
    "HEALTH INSURANCE CLAIM FORM",
    "APPROVED BY NATIONAL UNIFORM CLAIM COMMITTEE (NUCC) 02/12   CMS-1500",
    "1. MEDICARE [ ]  MEDICAID [ ]  GROUP HEALTH PLAN [X]",
    "1a. Insured's I.D. Number: XYZ123456789",
    "2. Patient's Name: Whitfield, Daniel R",
    "3. Patient's Birth Date: 04/12/1978   Sex: M",
    "4. Insured's Name: Whitfield, Daniel R",
    "10b. Auto Accident? [ ] Yes [X] No   Place (State): MA",
    "21. Diagnosis or Nature of Illness or Injury (ICD-10): A. S13.4XXA  B. M54.2",
    "24. Date(s) of Service: 08/14/2025 to 08/14/2025   Place of Service: 11",
    "24d. Procedures, Services, or Supplies (CPT/HCPCS): 99213, 97110",
    "25. Federal Tax I.D. Number: 04-3456789",
    "28. Total Charge: $385.00",
    "29. Amount Paid: $40.00",
    "31. Signature of Physician or Supplier: /s/ Alan Reyes, MD",
    "32. Service Facility: Plymouth Spine Clinic",
    "33. Billing Provider: Plymouth Spine Clinic, 12 Court St, Plymouth MA",
    "33a. Rendering Provider NPI: 1234567893",
]
#: The same page with every medical cue gone — what a poor OCR pass leaves —
#: plus the three shared insurance fields that won the customer's page for
#: auto_policy (policy number, insured name, signature) and the word "accident".
CMS_1500_NO_CUES_LINES = [
    "CLAIM FORM (illegible header)",
    "Policy Number: GHP-7781",
    "Insured Name: Whitfield, Daniel R",
    "Birth Date: 04/12/1978   Sex: M",
    "10b. Auto Accident? [ ] Yes [X] No   Place (State): MA",
    "21. Nature of Illness or Injury: A. S13.4XXA  B. M54.2",
    "24. Service from 08/14/2025 to 08/14/2025",
    "28. Charges: $385.00",
    "29. Paid: $40.00",
    "31. Signature of Physician or Supplier: /s/ Alan Reyes, MD",
    "The remaining boxes could not be read by the scanner and are left as they were on the form itself.",
]


def test_document_family_names_the_dominant_family_or_none():
    fam = fx.document_family("\n".join(CMS_1500_LINES))
    assert fam["family"] == "medical" and fam["counts"]["auto"] == 0 and "no competing family" in fam["basis"]
    assert {"cms-1500", "patient", "icd", "cpt", "npi"} <= set(fam["cues"])
    # The lender-facing property page: auto 3 (exclusions), property 2 — no family dominates, the gate stays open.
    fam = fx.document_family("\n".join(REAL_ESTATE_LINES))
    assert fam["family"] == "unknown" and fam["counts"] == {"auto": 3, "property": 2, "real_estate_transaction": 0, "medical": 0}
    assert fam["basis"].startswith("no family dominates (auto 3, property 2; margin over property is 1 < 2)")
    assert fx.document_family("")["family"] == "unknown"
    assert fx.document_family("Dear diary, today was fine.")["basis"] == "no family cue on the page"
    assert fx.document_family("grantor grantee deed parcel")["family"] == "real_estate_transaction"
    assert fx.type_allowed("auto_policy", "medical") is False and fx.type_allowed("medical_claim", "medical") is True
    assert fx.type_allowed("auto_policy", "unknown") is True and fx.type_allowed("uncertain", "medical") is True
    assert fx.type_allowed("medical_unknown", "medical") is True


def test_cms_1500_with_the_word_accident_is_a_medical_claim_with_its_fields():
    text = "\n".join(CMS_1500_LINES)
    cls = fx.classify_document(text)
    assert cls["document_type"] == "medical_claim" and cls["family"]["family"] == "medical"
    assert "family gate: medical" in cls["basis"] and cls["confidence"] <= fx.CLASSIFICATION_CAP
    assert "accident" not in cls["matched_keywords"]
    found = fx.found_field_names("medical_claim", [text])
    assert len(found) >= 6
    assert {"patient_name", "insured_id", "patient_dob", "diagnosis_codes", "procedure_codes", "provider_npi", "total_charge"} <= set(found)
    fields = _extract(text, "medical_claim")
    assert fields["patient_name"]["value"] == "Whitfield, Daniel R"
    assert fields["insured_id"]["value"] == "XYZ123456789" and fields["insured_id"]["compliance_bound"]
    assert fields["patient_dob"]["value"] == "1978-04-12"
    assert fields["insured_name"]["value"] == "Whitfield, Daniel R"
    assert fields["diagnosis_codes"]["value"] == ["S13.4XXA", "M54.2"] and fields["diagnosis_codes"]["field_type"] == "codes"
    assert fields["procedure_codes"]["value"] == ["99213", "97110"]
    assert fields["date_of_service"]["value"] == "2025-08-14"
    assert fields["provider_name"]["value"].startswith("Plymouth Spine Clinic")
    assert fields["provider_npi"]["value"] == "1234567893" and fields["federal_tax_id"]["value"] == "04-3456789"
    assert fields["total_charge"]["value"] == 385.0 and fields["amount_paid"]["value"] == 40.0
    # The auto schema finds nothing type-specific on this page: only a shared date.
    assert set(fx.type_specific_found("auto_policy", [text])) == set()
    assert fx.SHARED_FIELD_NAMES == {"policy_number", "insured_name", "signature", "effective_date", "expiration_date"}


def test_cms_1500_without_medical_cues_is_never_auto_policy():
    text = "\n".join(CMS_1500_NO_CUES_LINES)
    fam = fx.document_family(text)
    assert fam["counts"]["medical"] == 0 and fam["family"] == "unknown"
    cls = fx.classify_document(text)
    assert cls["document_type"] == "uncertain" and len(cls["matched_keywords"]) == 1  # one stray keyword, as in the customer's `detected`
    # The shared fields *are* on the page — that is exactly what used to win it for auto_policy.
    assert set(fx.found_field_names("auto_policy", [text])) >= {"policy_number", "insured_name"}
    assert fx.type_specific_found("auto_policy", [text]) == []


def test_codes_are_parsed_as_lists_and_local_ocr_confidence_names_itself():
    assert fx._parse_codes("A. S13.4XXA  B. M54.2") == ["S13.4XXA", "M54.2"]
    assert fx._parse_codes("99213, 97110-59") == ["99213", "97110-59"]
    assert fx._parse_codes("none") is None
    # Field-level confidence: a low page (0.28) whose OCR read one line well.
    bundle = {"jdf": {"pages": [{"pageSize": {"width": 210, "height": 297}, "elements": [
        {"id": "el-1", "type": "image", "position": {"x": 10, "y": 10}, "width": 100, "ocr": {"blocks": [{"text": "Policy Number: AP-1", "confidence": 0.91}]}},
        {"id": "el-2", "type": "image", "position": {"x": 10, "y": 30}, "width": 100, "ocr": {"blocks": [{"text": "Named Insured: John Q. Sample", "confidence": 0.30}]}},
        {"id": "el-3", "type": "text", "position": {"x": 10, "y": 50}, "width": 100, "content": "Agent: Mary Agent"},
    ]}]}}
    layout = fx.page_layout(bundle)
    assert [seg["ocr_confidence"] for seg in layout[0]] == [0.91, 0.3, None]
    fields = {f["name"]: f for f in fx.extract_fields("auto_policy", fx.page_texts(bundle), layout=layout, parser_name="jdf-cli",
                                                      parse_confidence=None, ocr_confidence=None, page_quality=[0.28])}
    high, low, page = fields["policy_number"], fields["insured_name"], fields["agent_name"]
    assert high["extraction_confidence"] > low["extraction_confidence"]
    assert "local_ocr (0.91)" in high["confidence_basis"] and high["quality_source"] == "local_ocr" and high["local_quality"] == 0.91
    assert "local_ocr (0.30)" in low["confidence_basis"] and high["confidence_basis"] != low["confidence_basis"]
    assert "page_quality (0.28)" in page["confidence_basis"] and page["quality_source"] == "page_quality" and page["local_quality"] is None
    assert high["extraction_confidence"] == pytest.approx(0.85 * 0.91, abs=0.01) and page["extraction_confidence"] == pytest.approx(0.85 * 0.28, abs=0.01)
    # Absent fields on this parse are anchored to the first layout node and list what was searched.
    absent = fields["vin"]
    assert absent["field_source_node_id"] == "el-1" and absent["evidence"]["anchor_node_id"] == "el-1"
    assert absent["evidence"]["searched_node_ids"] == ["el-1", "el-2", "el-3"] and absent["source_span"] == {"span_type": "absent", "pages": [1]}
    assert absent["evidence_state"] == "unreadable"  # 3 short lines, quality 0.28


def test_schema_mismatch_fields_do_not_ask_for_review_and_have_no_confidence():
    fields = list(_extract().values())
    for f in fields:
        fx.apply_decision_policy(f)
    assert {f["evidence_state"] for f in fields if f["value"] is not None} <= {"found_verified", "found_unverified"}
    assert fields[0]["evidence_state"] in ("found_verified", "found_unverified")
    fx.mark_schema_mismatch(fields)
    for f in fields:
        assert f["evidence_state"] == "schema_mismatch" and f["extraction_confidence"] is None
        assert f["confidence_basis"] == "not computed: schema mismatch" and f["reason"] == "Wrong document type — fields not applicable"
        assert fx.field_needs_review(f) is False
        fx.apply_decision_policy(f)  # a second policy pass leaves them alone
        assert f["evidence_state"] == "schema_mismatch" and f["routing_action"] == "none"
    assert fx.cross_document_conflicts([{"report_id": "a", "fields": fields}, {"report_id": "b", "fields": [{"name": "policy_number", "value": "OTHER"}]}]) == []
    # A Z3 violation on the anchor node of an absent field is not a violation of that field.
    absent = fx.extract_fields("auto_policy", ["Policy Number: AP-1"], layout=[[{"start": 0, "end": 19, "text": "Policy Number: AP-1", "node_id": "n1", "bbox": None}]],
                               parser_name="jdf-cli", parse_confidence=None, ocr_confidence=None, page_quality=[1.0])
    vin = next(f for f in absent if f["name"] == "vin")
    assert vin["field_source_node_id"] == "n1"
    fx.attach_z3_violations(absent, [{"description": "x", "node_id": "n1"}])
    assert vin["z3_violation"] is False and next(f for f in absent if f["name"] == "policy_number")["z3_violation"] is True
