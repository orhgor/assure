"""Claim verdicts (policy ``claim-v1``), the gate they drive, and the cache key.

Every checker here is injected — no network. The transport is not under test; the
rule order in ``services/claim_policy.derive_claim`` is.
"""

from __future__ import annotations

from typing import Any

import pytest

from prompt_matrix.services.audit_summary import (
    _provenance_counts,
    build_audit_summary,
    compute_gate_status,
    provenance_gate_fields,
)
from prompt_matrix.services.claim_policy import (
    CLAIM_POLICY_ID,
    CONTRADICTED,
    INSUFFICIENT_EVIDENCE,
    UNSUPPORTED,
    VERIFIED,
    attach_claims_to_tree,
    derive_claim,
    is_claim_eligible,
    page_of,
)
from prompt_matrix.services.entailment import attach_entailment_to_tree
from prompt_matrix.services.source_carry import carry_plan

# A source long enough to clear the 200-character floor, with the sentences the
# claims below cite.
SOURCE = (
    "COMMERCIAL PROPERTY POLICY. Declarations. The policy liability limit is set at "
    "$5,000,000 for combined single limit. The deductible is $25,000 per occurrence. "
    "Flood is excluded. The annual premium is $1,250.00 and the policy fee is $250. "
    "The policy period runs from 01/15/2025 to 01/15/2026. Vacant properties exceeding "
    "60 consecutive days require referral to the underwriter before coverage applies."
)
ROW = {"id": "sub-1", "filename": "policy.pdf", "extracted_text": SOURCE, "parse_confidence": 0.93}


def _paragraph(node_id: str, content: str, quote: str | None, *, page: Any = None, source_id: str = "sub-1") -> dict:
    provenance = []
    if quote is not None:
        row: dict[str, Any] = {
            "source_type": "internal_doc",
            "source_name": "policy.pdf",
            "source_id": source_id,
            "extracted_quote": quote,
        }
        if page is not None:
            row["page_number"] = page
        provenance.append(row)
    return {
        "type": "paragraph",
        "id": node_id,
        "content": content,
        "entities_referenced": [],
        "provenance": provenance,
        "meta": {},
        "annotations": {"redhat": [], "z3": []},
    }


def _document(*paragraphs: dict) -> dict:
    return {
        "document_id": "doc-claims",
        "meta": {},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "sec-1",
                "title": "Summary",
                "children": list(paragraphs),
                "meta": {},
                "annotations": {"redhat": [], "z3": []},
            }
        ],
    }


def _stub(verdicts: dict[str, str], evidence: dict[str, str] | None = None):
    """A ``contradicts`` carries the conflicting source text (the window itself
    unless ``evidence`` says otherwise); without verbatim evidence it is
    downgraded to ``no`` by ``entailment.enforce_contradiction_evidence``."""

    def check(claim: str, source: str) -> dict[str, Any]:
        record = {
            "verdict": verdicts[claim],
            "reasoning": "stubbed",
            "model": "stub/model",
            "checked_at": "2026-09-27T00:00:00+00:00",
        }
        if verdicts[claim] == "contradicts":
            record["evidence"] = (evidence or {}).get(claim, source)
        return record

    return check


def _claim(doc: dict, node_id: str) -> dict:
    node = next(n for n in doc["body"][0]["children"] if n["id"] == node_id)
    return node["meta"]["provenance"]["claim"]


def _judge(doc: dict, verdicts: dict[str, str], sources: list[dict] | None = None) -> dict:
    attach_entailment_to_tree(doc, checker=_stub(verdicts))
    rows = [ROW] if sources is None else sources
    attach_claims_to_tree(doc, sources=rows, carry_plan=carry_plan(rows))
    return doc


# --------------------------------------------------------------------------- #
# The four verdicts
# --------------------------------------------------------------------------- #


def test_the_four_verdicts_and_the_block_shape() -> None:
    claims = {
        "yes": ("The policy liability limit is $5,000,000 combined single limit.", "The policy liability limit is set at $5,000,000 for combined single limit."),
        "no": ("The policy carries a $250,000 cyber sublimit.", "The deductible is $25,000 per occurrence."),
        "contradicts": ("Flood is covered under the policy.", "Flood is excluded."),
        "unverified": ("The annual premium is $1,250.00.", "The annual premium is $1,250.00 and the policy fee is $250."),
    }
    doc = _document(*[_paragraph(f"p-{v}", text, quote, page="3") for v, (text, quote) in claims.items()])
    _judge(doc, {text: v for v, (text, _q) in claims.items()})

    yes = _claim(doc, "p-yes")
    assert yes["verdict"] == VERIFIED
    assert yes["policy"] == CLAIM_POLICY_ID
    assert yes["quote_verbatim"] is True
    assert yes["quote"] == claims["yes"][1]
    assert yes["source_id"] == "sub-1" and yes["source_name"] == "policy.pdf"
    assert yes["page"] == 3
    assert yes["checks"]["entailment"] == "yes"
    assert yes["checks"]["numeric"]["status"] == "not_applicable"
    assert yes["checks"]["source_quality"]["status"] == "ok"
    assert yes["flags"] == []
    assert set(yes) == {
        "policy", "verdict", "reason", "quote", "quote_verbatim", "source_id",
        "source_name", "page", "checks", "flags",
    }
    assert set(yes["checks"]) == {"kind", "entailment", "numeric", "wording", "source_quality"}
    assert yes["checks"]["kind"] == "fact"
    # No model confidence anywhere in the block.
    assert "confidence" not in str(yes).lower().replace("parse confidence", "")

    assert _claim(doc, "p-no")["verdict"] == UNSUPPORTED
    assert _claim(doc, "p-contradicts")["verdict"] == CONTRADICTED
    assert _claim(doc, "p-contradicts")["reason"] == "the source states otherwise"
    # "covered" is high-risk wording the quote does not carry.
    assert "high_risk_wording:covered" in _claim(doc, "p-contradicts")["flags"]
    unverified = _claim(doc, "p-unverified")
    assert unverified["verdict"] == INSUFFICIENT_EVIDENCE
    assert unverified["reason"] == "verifier did not answer"


def test_partial_is_unsupported_never_verified() -> None:
    text = "The deductible is $25,000 per occurrence for all locations."
    doc = _document(_paragraph("p1", text, "The deductible is $25,000 per occurrence."))
    _judge(doc, {text: "partial"})
    block = _claim(doc, "p1")
    assert block["verdict"] == UNSUPPORTED
    assert block["reason"] == "a material qualifier is missing"
    assert "high_risk_wording:all" in block["flags"]


def test_no_anchor_is_unsupported() -> None:
    text = "The policy includes $250,000 of cyber coverage for network operations."
    doc = _document(_paragraph("p1", text, None))
    attach_claims_to_tree(doc, sources=[ROW])
    block = _claim(doc, "p1")
    assert block["verdict"] == UNSUPPORTED
    assert block["reason"] == "no source sentence carries this claim"
    assert block["quote"] is None and block["quote_verbatim"] is False


def test_a_quote_that_is_not_verbatim_is_never_verified() -> None:
    """The entailment model says yes; the cited text is not in the source. UNSUPPORTED."""
    text = "The policy liability limit is $5,000,000 combined single limit."
    doc = _document(_paragraph("p1", text, "The policy liability limit is $5,000,000 (combined single limit)."))
    _judge(doc, {text: "yes"})
    block = _claim(doc, "p1")
    assert block["verdict"] == UNSUPPORTED
    assert block["reason"] == "the cited text is not verbatim in the source"
    assert block["quote"] is None and block["quote_verbatim"] is False


def test_whitespace_differences_do_not_break_verbatim() -> None:
    text = "The deductible is $25,000 per occurrence."
    doc = _document(_paragraph("p1", text, "The deductible is  $25,000\nper occurrence."))
    _judge(doc, {text: "yes"})
    assert _claim(doc, "p1")["verdict"] == VERIFIED


def test_page_is_never_defaulted() -> None:
    text = "The deductible is $25,000 per occurrence."
    quote = "The deductible is $25,000 per occurrence."
    doc = _document(
        _paragraph("p-none", text, quote),
        _paragraph("p-empty", text, quote, page=""),
        _paragraph("p-zero", text, quote, page="0"),
        _paragraph("p-seven", text, quote, page="7"),
    )
    _judge(doc, {text: "yes"})
    assert _claim(doc, "p-none")["page"] is None
    assert _claim(doc, "p-empty")["page"] is None
    assert _claim(doc, "p-zero")["page"] is None
    assert _claim(doc, "p-seven")["page"] == 7
    assert page_of({"page": None}) is None and page_of({"page": 2}) == 2


# --------------------------------------------------------------------------- #
# Source quality
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("row", "basis_fragment"),
    [
        ({**ROW, "parse_confidence": 0.42}, "parse confidence 0.42 is under 0.5"),
        ({**ROW, "extracted_text": "Flood is excluded."}, "characters of text (under 200)"),
    ],
)
def test_low_source_quality_is_insufficient_evidence(row: dict, basis_fragment: str) -> None:
    text = "Flood is excluded."
    doc = _document(_paragraph("p1", text, "Flood is excluded."))
    _judge(doc, {text: "yes"}, sources=[row])
    block = _claim(doc, "p1")
    assert block["verdict"] == INSUFFICIENT_EVIDENCE
    assert block["checks"]["source_quality"]["status"] == "low"
    assert basis_fragment in block["checks"]["source_quality"]["basis"]
    assert block["quote_verbatim"] is True, "the quote is verbatim; the source is what is weak"


def test_a_dropped_source_is_insufficient_evidence() -> None:
    text = "Flood is excluded."
    doc = _document(_paragraph("p1", text, "Flood is excluded."))
    attach_entailment_to_tree(doc, checker=_stub({text: "yes"}))
    plan = carry_plan([ROW])
    plan["sources"][0]["included"] = False
    plan["sources"][0]["dropped_reason"] = "total cap"
    attach_claims_to_tree(doc, sources=[ROW], carry_plan=plan)
    block = _claim(doc, "p1")
    assert block["verdict"] == INSUFFICIENT_EVIDENCE
    assert "not carried into the compile" in block["checks"]["source_quality"]["basis"]


def test_a_source_not_supplied_is_insufficient_evidence() -> None:
    text = "Flood is excluded."
    doc = _document(_paragraph("p1", text, "Flood is excluded.", source_id="sub-other"))
    doc["body"][0]["children"][0]["provenance"][0]["source_name"] = "other.pdf"
    _judge(doc, {text: "yes"})
    block = _claim(doc, "p1")
    assert block["verdict"] == INSUFFICIENT_EVIDENCE
    assert block["checks"]["source_quality"]["status"] == "missing"
    doc2 = _document(_paragraph("p1", text, "Flood is excluded."))
    attach_entailment_to_tree(doc2, checker=_stub({text: "yes"}))
    attach_claims_to_tree(doc2, sources=None)
    assert _claim(doc2, "p1")["verdict"] == INSUFFICIENT_EVIDENCE


def test_no_entailment_answer_is_insufficient_evidence() -> None:
    text = "Flood is excluded."
    doc = _document(_paragraph("p1", text, "Flood is excluded."))
    attach_claims_to_tree(doc, sources=[ROW])
    block = _claim(doc, "p1")
    assert block["verdict"] == INSUFFICIENT_EVIDENCE
    assert block["reason"] == "no entailment check ran for this claim"
    assert block["checks"]["entailment"] is None


# --------------------------------------------------------------------------- #
# Numeric and wording
# --------------------------------------------------------------------------- #


def test_a_numeric_mismatch_contradicts_even_when_entailment_says_yes() -> None:
    text = "The annual premium of $1,250.00 and the policy fee of $250 total $1,600."
    doc = _document(_paragraph("p1", text, "The annual premium is $1,250.00 and the policy fee is $250."))
    _judge(doc, {text: "yes"})
    block = _claim(doc, "p1")
    assert block["verdict"] == CONTRADICTED
    assert block["checks"]["numeric"]["status"] == "mismatch"
    assert block["checks"]["numeric"]["expected"] == "1500"


def test_a_missing_figure_is_unsupported_not_contradicted() -> None:
    """A figure the source lacks cannot be found — UNSUPPORTED — unless the source
    names the same quantity with another value, which is a conflict."""
    text = "The policy carries a $250,000 cyber sublimit."
    doc = _document(_paragraph("p1", text, "The deductible is $25,000 per occurrence."))
    _judge(doc, {text: "yes"})
    block = _claim(doc, "p1")
    assert block["verdict"] == UNSUPPORTED
    assert block["checks"]["numeric"]["kind"] == "missing_figure"
    assert "$250,000" in block["reason"]

    text2 = "The deductible is $50,000 per occurrence."
    doc2 = _document(_paragraph("p1", text2, "The deductible is $25,000 per occurrence."))
    _judge(doc2, {text2: "yes"})
    block2 = _claim(doc2, "p1")
    assert block2["verdict"] == CONTRADICTED
    assert block2["checks"]["numeric"]["kind"] == "arithmetic"
    assert block2["checks"]["numeric"]["expected"] == "$25,000"


def test_a_recomputed_total_verifies() -> None:
    text = "The annual premium of $1,250.00 and the policy fee of $250 total $1,500."
    doc = _document(_paragraph("p1", text, "The annual premium is $1,250.00 and the policy fee is $250."))
    _judge(doc, {text: "yes"})
    block = _claim(doc, "p1")
    assert block["verdict"] == VERIFIED
    assert block["checks"]["numeric"]["status"] == "recomputed_ok"


def test_a_date_span_is_recomputed_from_the_source_dates() -> None:
    text = "The policy period runs from 01/15/2025 to 01/15/2026, 12 months."
    doc = _document(_paragraph("p1", text, "The policy period runs from 01/15/2025 to 01/15/2026."))
    _judge(doc, {text: "yes"})
    assert _claim(doc, "p1")["verdict"] == VERIFIED


def test_high_risk_wording_flags_but_does_not_change_a_verified_verdict() -> None:
    text = "Vacant properties exceeding 60 consecutive days require referral before coverage applies."
    quote = "Vacant properties exceeding 60 consecutive days require referral to the underwriter before coverage applies."
    doc = _document(_paragraph("p1", text, quote))
    _judge(doc, {text: "yes"})
    block = _claim(doc, "p1")
    assert block["verdict"] == VERIFIED
    # The quote carries "coverage applies", so it is not flagged.
    assert block["flags"] == []

    text2 = "Coverage is guaranteed for vacant properties."
    doc2 = _document(_paragraph("p2", text2, quote))
    _judge(doc2, {text2: "yes"})
    block2 = _claim(doc2, "p2")
    assert block2["verdict"] == VERIFIED
    assert block2["flags"] == ["high_risk_wording:guaranteed"]
    assert block2["checks"]["wording"]["unsupported_terms"] == ["guaranteed"]


# --------------------------------------------------------------------------- #
# Calibration from the live run of 2026-09-27
# --------------------------------------------------------------------------- #

DECLARATIONS = (
    "AUTO POLICY DECLARATIONS\nPolicy Number: AP-2025-0001\nNamed Insured: John Q. Sample\n"
    "Policy Period: 01/15/2025 to 01/15/2026\nVehicle: 2003 Honda Accord\nVIN: 1HGCM82633A004352\n"
    "Total Premium: $1,250.00\nLiability Limit: $100,000\nCollision Deductible: $500\n"
    "Comprehensive Deductible: $250\nAgent: Mary Agent\nFlood damage is excluded under this policy.\n"
    "Authorized Signature: /s/ Mary Agent\n"
    + "Coverage notes and conditions apply as stated in the policy forms.\n" * 4
)
DECL_ROW = {"id": "sub-decl", "filename": "declarations.pdf", "extracted_text": DECLARATIONS, "parse_confidence": 0.99}


def _decl_paragraph(node_id: str, content: str, quote: str) -> dict:
    node = _paragraph(node_id, content, quote, source_id="sub-decl")
    node["provenance"][0]["source_name"] = "declarations.pdf"
    return node


def test_a_contradiction_without_verbatim_evidence_is_downgraded_to_not_stated() -> None:
    """Live misfire: 'auto policy with the number AP-2025-0001' vs 'Policy Number:
    AP-2025-0001' came back ``contradicts``. Absence from the window is ``no``."""
    text = "The policy in question is an auto policy with the number AP-2025-0001."
    doc = _document(_decl_paragraph("p1", text, "Policy Number: AP-2025-0001"))
    attach_entailment_to_tree(doc, checker=_stub({text: "contradicts"}, evidence={text: "this text is not in the source"}))
    record = doc["body"][0]["children"][0]["meta"]["provenance"]["entailment"]
    assert record["verdict"] == "no"
    assert record["contradicted"] is False
    assert record["citations"][0]["downgraded_from"] == "contradicts"
    attach_claims_to_tree(doc, sources=[DECL_ROW])
    assert _claim(doc, "p1")["verdict"] == UNSUPPORTED
    assert _claim(doc, "p1")["verdict"] != CONTRADICTED


def test_a_contradiction_with_verbatim_evidence_stands() -> None:
    text = "The collision deductible is $250."
    doc = _document(_decl_paragraph("p1", text, "Collision Deductible: $500"))
    attach_entailment_to_tree(doc, checker=_stub({text: "contradicts"}, evidence={text: "Collision  Deductible: $500"}))
    assert doc["body"][0]["children"][0]["meta"]["provenance"]["entailment"]["verdict"] == "contradicts"
    attach_claims_to_tree(doc, sources=[DECL_ROW])
    assert _claim(doc, "p1")["verdict"] == CONTRADICTED


def test_absence_in_one_window_beside_yes_in_another_is_yes() -> None:
    text = "The policy AP-2025-0001 is issued to John Q. Sample."
    node = _decl_paragraph("p1", text, "Policy Number: AP-2025-0001")
    node["provenance"].append({**node["provenance"][0], "extracted_quote": "Named Insured: John Q. Sample"})

    def check(claim: str, source: str) -> dict[str, Any]:
        return {"verdict": "yes" if "Insured" in source else "no", "reasoning": "r", "model": "m", "checked_at": "t"}

    doc = _document(node)
    attach_entailment_to_tree(doc, checker=check)
    assert doc["body"][0]["children"][0]["meta"]["provenance"]["entailment"]["verdict"] == "yes"
    attach_claims_to_tree(doc, sources=[DECL_ROW])
    assert _claim(doc, "p1")["verdict"] == VERIFIED


def test_listed_amounts_are_not_summed_against_an_unrelated_total() -> None:
    text = "The liability limit is $100,000, the collision deductible is $500 and the comprehensive deductible is $250."
    doc = _document(_decl_paragraph("p1", text, "Liability Limit: $100,000"))
    _judge(doc, {text: "yes"}, sources=[DECL_ROW])
    block = _claim(doc, "p1")
    assert block["checks"]["kind"] == "enumeration"
    assert block["verdict"] == VERIFIED, block["reason"]
    assert all(sc["status"] == "verified" for sc in block["checks"]["sub_claims"])


def test_collision_and_comprehensive_deductibles_are_not_an_inconsistency() -> None:
    text_a = "The collision deductible is $500."
    text_b = "The comprehensive deductible is $250."
    doc = _document(
        _decl_paragraph("a", text_a, "Collision Deductible: $500"),
        _decl_paragraph("b", text_b, "Comprehensive Deductible: $250"),
    )
    _judge(doc, {text_a: "yes", text_b: "yes"}, sources=[DECL_ROW])
    fields = provenance_gate_fields(document=doc, z3_status="PASS", redhat_count=0, has_substrate=True, sources=[DECL_ROW])
    assert fields["claim_summary"]["inconsistencies"] == []
    assert fields["claim_summary"]["flagged"] == 0
    assert fields["ok"] is True


def test_an_enumeration_is_assessed_per_sub_claim_against_the_whole_source() -> None:
    text = (
        "Key policy details are as follows: policy number AP-2025-0001, named insured John Q. Sample, "
        "policy period 01/15/2025 to 01/15/2026, vehicle 2003 Honda Accord, VIN 1HGCM82633A004352, "
        "total premium $1,250, liability limit $100,000, collision deductible $500, comprehensive deductible $250, "
        "agent Mary Agent."
    )
    # Anchored to one line only — the window cannot carry eight facts.
    doc = _document(_decl_paragraph("p1", text, "Policy Number: AP-2025-0001"))
    _judge(doc, {text: "no"}, sources=[DECL_ROW])
    block = _claim(doc, "p1")
    assert block["checks"]["kind"] == "enumeration"
    assert block["verdict"] == VERIFIED, block["reason"]
    statuses = {sc["text"]: sc["status"] for sc in block["checks"]["sub_claims"]}
    assert all(status == "verified" for status in statuses.values()), statuses
    assert block["quote_verbatim"] is True
    assert block["checks"]["numeric"]["status"] == "not_applicable"

    # One wrong fact: UNSUPPORTED naming it. A same-label conflict: CONTRADICTED.
    wrong = text.replace("agent Mary Agent", "agent Jane Broker")
    doc2 = _document(_decl_paragraph("p1", wrong, "Policy Number: AP-2025-0001"))
    _judge(doc2, {wrong: "no"}, sources=[DECL_ROW])
    assert _claim(doc2, "p1")["verdict"] == UNSUPPORTED
    assert "Jane Broker" in _claim(doc2, "p1")["reason"]
    conflict = text.replace("collision deductible $500", "collision deductible $600")
    doc3 = _document(_decl_paragraph("p1", conflict, "Policy Number: AP-2025-0001"))
    _judge(doc3, {conflict: "no"}, sources=[DECL_ROW])
    assert _claim(doc3, "p1")["verdict"] == CONTRADICTED
    assert "collision deductible as $500" in _claim(doc3, "p1")["reason"]


def test_an_unanchored_enumeration_is_still_assessed_against_the_supplied_sources() -> None:
    text = "Policy number AP-2025-0001, total premium $1,250.00, liability limit $100,000, agent Mary Agent."
    doc = _document(_paragraph("p1", text, None))
    attach_claims_to_tree(doc, sources=[DECL_ROW])
    block = _claim(doc, "p1")
    assert block["checks"]["kind"] == "enumeration"
    assert block["verdict"] == VERIFIED, block["reason"]
    assert block["quote"] and block["quote_verbatim"] is True
    assert block["source_name"] == "declarations.pdf"


@pytest.mark.parametrize(
    "text",
    [
        "The information provided is directly from the source material and is accurate.",
        "The source does not provide information on the comprehensive deductible.",
        "This summary reflects the declarations page as supplied.",
        "Based on the source, no further details are available.",
        # From the live run of 2026-09-27, all of which first read as facts.
        "missing items\nThe source material does not provide information on the location, loss date or endorsements.",
        "evidence table\nThe source material provides the following evidence: policy number, named insured, agent.",
        "confidence\nThe confidence in the extracted information is high, as the source material is clear.",
        "confidence\nThe information extracted is based directly on the provided source material and is presented exactly as stated.",
        "missing items\nNo location, loss date or cause of loss appear anywhere.",
    ],
)
def test_meta_statements_are_not_claims(text: str) -> None:
    """Meta paragraphs are part of the memo template; a verdict on them would hold
    every document at review. ``verdict: null``, counted in ``claim_summary.meta``."""
    doc = _document(_decl_paragraph("p1", text, "Policy Number: AP-2025-0001"))
    _judge(doc, {text: "yes"}, sources=[DECL_ROW])
    block = _claim(doc, "p1")
    assert block["verdict"] is None
    assert block["checks"]["kind"] == "meta"
    assert block["reason"] == "statement about the source, not a document fact"
    fields = provenance_gate_fields(document=doc, z3_status="PASS", redhat_count=0, has_substrate=True, sources=[DECL_ROW])
    assert fields["claim_summary"]["meta"] == 1
    assert fields["claim_summary"]["total"] == 0
    assert fields["provenance_stats"]["unsupported"] == 0
    assert fields["provenance_stats"]["meta"] == 1


def test_a_meta_statement_after_a_label_line_is_still_meta() -> None:
    """The compiled draft opens each paragraph with a label line; the statement
    follows it (live run 2026-09-27)."""
    text = "missing items\nThe source does not provide information on the loss date or the cause of loss."
    doc = _document(_paragraph("p1", text, None))
    attach_claims_to_tree(doc, sources=[DECL_ROW])
    assert _claim(doc, "p1")["checks"]["kind"] == "meta"
    assert _claim(doc, "p1")["verdict"] is None


def test_meta_paragraphs_never_hold_the_gate() -> None:
    fact = "Flood damage is excluded under this policy."
    doc = _document(
        _decl_paragraph("f", fact, "Flood damage is excluded under this policy."),
        _paragraph("m1", "missing items\nThe source does not provide the loss date.", None),
        _paragraph("m2", "confidence\nThe information extracted is based directly on the source.", None),
    )
    _judge(doc, {fact: "yes"}, sources=[DECL_ROW])
    fields = provenance_gate_fields(document=doc, z3_status="PASS", redhat_count=0, has_substrate=True, sources=[DECL_ROW])
    assert fields["claim_summary"] == {
        "total": 1, "verified": 1, "unsupported": 0, "contradicted": 0, "insufficient": 0,
        "flagged": 0, "paragraphs": 1, "meta": 2, "policy": CLAIM_POLICY_ID, "inconsistencies": [],
    }
    assert fields["ok"] is True and fields["gate_status"] == "pass"


# --------------------------------------------------------------------------- #
# Sentences — the claim unit
# --------------------------------------------------------------------------- #


def test_a_multi_sentence_paragraph_is_assessed_per_sentence() -> None:
    """The memo shape: several sentences of different facts in one paragraph, each
    with its own citation. One unanchored or unsupported sentence must not make
    the whole paragraph UNSUPPORTED silently — each sentence carries its verdict."""
    s1 = "The policy number is AP-2025-0001."
    s2 = "The total premium is $1,250."
    s3 = "Flood damage is excluded under this policy."
    text = f"claim snapshot\n{s1} {s2} {s3}"
    node = _paragraph("p1", text, None)
    node["provenance"] = [
        {"extracted_quote": "Policy Number: AP-2025-0001", "source_name": "declarations.pdf", "page": 1, "cited_id": "S2", "sentence_index": 0},
        {"extracted_quote": "Total Premium: $1,250.00", "source_name": "declarations.pdf", "page": 1, "cited_id": "S7", "sentence_index": 1},
        {"extracted_quote": "Flood damage is excluded under this policy.", "source_name": "declarations.pdf", "page": 1, "cited_id": "S12", "sentence_index": 2},
    ]
    calls: list[tuple[str, str]] = []

    def check(claim: str, source: str) -> dict[str, Any]:
        calls.append((claim, source))
        return {"verdict": "yes", "reasoning": "r", "model": "m", "checked_at": "t"}

    doc = _document(node)
    attach_entailment_to_tree(doc, checker=check)
    # One call per (sentence, its own window) — never the paragraph against a window.
    assert sorted(calls) == sorted([
        (s1, "Policy Number: AP-2025-0001"),
        (s2, "Total Premium: $1,250.00"),
        (s3, "Flood damage is excluded under this policy."),
    ])
    record = doc["body"][0]["children"][0]["meta"]["provenance"]["entailment"]
    assert [e["verdict"] for e in record["sentences"]] == ["yes", "yes", "yes"]
    attach_claims_to_tree(doc, sources=[DECL_ROW])
    block = _claim(doc, "p1")
    assert block["checks"]["kind"] == "sentences"
    assert block["verdict"] == VERIFIED, block["reason"]
    assert [u["text"] for u in block["checks"]["sub_claims"]] == [s1, s2, s3]
    assert all(u["verdict"] == VERIFIED and u["quote_verbatim"] for u in block["checks"]["sub_claims"])
    assert block["checks"]["sub_claims"][1]["quote"] == "Total Premium: $1,250.00"
    assert block["checks"]["sub_claims"][1]["page"] == 1
    fields = provenance_gate_fields(document=doc, z3_status="PASS", redhat_count=0, has_substrate=True, sources=[DECL_ROW])
    assert fields["claim_summary"]["total"] == 3
    assert fields["claim_summary"]["paragraphs"] == 1
    assert fields["claim_summary"]["verified"] == 3
    assert fields["ok"] is True


def test_one_failing_sentence_names_itself_and_the_paragraph_follows() -> None:
    s1 = "The policy number is AP-2025-0001."
    s2 = "The policy carries a $250,000 cyber sublimit."
    text = f"{s1} {s2}"
    node = _paragraph("p1", text, None)
    node["provenance"] = [
        {"extracted_quote": "Policy Number: AP-2025-0001", "source_name": "declarations.pdf", "page": 1, "sentence_index": 0},
    ]
    doc = _document(node)
    attach_entailment_to_tree(doc, checker=lambda c, s: {"verdict": "yes", "reasoning": "r", "model": "m", "checked_at": "t"})
    attach_claims_to_tree(doc, sources=[DECL_ROW])
    block = _claim(doc, "p1")
    assert block["checks"]["kind"] == "sentences"
    assert block["verdict"] == UNSUPPORTED
    assert "$250,000" in block["reason"] or "cyber" in block["reason"]
    units = block["checks"]["sub_claims"]
    assert units[0]["verdict"] == VERIFIED
    assert units[1]["verdict"] == UNSUPPORTED
    fields = provenance_gate_fields(document=doc, z3_status="PASS", redhat_count=0, has_substrate=True, sources=[DECL_ROW])
    assert fields["claim_summary"]["total"] == 2
    assert fields["claim_summary"]["verified"] == 1
    assert fields["claim_summary"]["unsupported"] == 1
    assert fields["provenance_stats"]["unsupported"] == 1  # paragraph level

    # A contradicted sentence makes the paragraph CONTRADICTED.
    s3 = "The collision deductible is $600."
    node2 = _paragraph("p2", f"{s1} {s3}", None)
    node2["provenance"] = [
        {"extracted_quote": "Policy Number: AP-2025-0001", "source_name": "declarations.pdf", "page": 1, "sentence_index": 0},
        {"extracted_quote": "Collision Deductible: $500", "source_name": "declarations.pdf", "page": 1, "sentence_index": 1},
    ]
    doc2 = _document(node2)
    attach_entailment_to_tree(doc2, checker=lambda c, s: {"verdict": "yes", "reasoning": "r", "model": "m", "checked_at": "t"})
    attach_claims_to_tree(doc2, sources=[DECL_ROW])
    assert _claim(doc2, "p2")["verdict"] == CONTRADICTED
    assert _claim(doc2, "p2")["checks"]["sub_claims"][1]["verdict"] == CONTRADICTED


def test_an_uncited_sentence_falls_back_to_the_source_text() -> None:
    """A sentence with no citation is searched in the cited sources: verbatim, or
    its values under their labels."""
    s1 = "The policy number is AP-2025-0001."
    s2 = "Flood damage is excluded under this policy."
    node = _paragraph("p1", f"{s1} {s2}", None)
    node["provenance"] = [
        {"extracted_quote": "Policy Number: AP-2025-0001", "source_name": "declarations.pdf", "page": 1, "sentence_index": 0},
    ]
    doc = _document(node)
    attach_entailment_to_tree(doc, checker=lambda c, s: {"verdict": "yes", "reasoning": "r", "model": "m", "checked_at": "t"})
    attach_claims_to_tree(doc, sources=[DECL_ROW])
    block = _claim(doc, "p1")
    assert block["verdict"] == VERIFIED, block["reason"]
    unit = block["checks"]["sub_claims"][1]
    assert unit["verdict"] == VERIFIED
    assert unit["quote"] == "Flood damage is excluded under this policy."
    assert "without a citation" in unit["reason"]


def test_citation_rows_are_assigned_to_the_sentence_they_end() -> None:
    from prompt_matrix.services.claim_policy import sentence_for_offset, sentence_units

    text = "claim snapshot\nThe policy number is AP-2025-0001 [S2]. The total premium is $1,250 [S7]."
    import re
    positions = [m.start() for m in re.finditer(r"\[S\d+\]", text)]
    blanked = re.sub(r"\[S\d+\]", lambda m: " " * len(m.group(0)), text)
    assert [sentence_for_offset(blanked, p) for p in positions] == [0, 1]
    # Rows without an index go to the sentence they overlap.
    node = _paragraph("p1", "The policy number is AP-2025-0001. The total premium is $1,250.", None)
    node["provenance"] = [
        {"extracted_quote": "Total Premium: $1,250.00", "source_name": "declarations.pdf"},
        {"extracted_quote": "Policy Number: AP-2025-0001", "source_name": "declarations.pdf"},
    ]
    units = sentence_units(node)
    assert [r["extracted_quote"] for r in units[0]["rows"]] == ["Policy Number: AP-2025-0001"]
    assert [r["extracted_quote"] for r in units[1]["rows"]] == ["Total Premium: $1,250.00"]


def test_a_contradiction_reasoned_as_absence_is_downgraded() -> None:
    """The model quoted the whole window as EVIDENCE (so it is verbatim) while its
    REASON said the SOURCE does not mention the VIN, premium, … — an absence."""
    text = "The vehicle is a 2003 Honda Accord with a VIN of 1HGCM82633A004352 and the total premium is $1,250."
    node = _decl_paragraph("p1", text, "Vehicle: 2003 Honda Accord")

    def check(claim: str, source: str) -> dict[str, Any]:
        return {
            "verdict": "contradicts",
            "reasoning": "The SOURCE does not mention the VIN, total premium or liability limit.",
            "evidence": source,
            "model": "m",
            "checked_at": "t",
        }

    doc = _document(node)
    attach_entailment_to_tree(doc, checker=check)
    record = doc["body"][0]["children"][0]["meta"]["provenance"]["entailment"]
    assert record["verdict"] == "no"
    assert record["citations"][0]["downgraded_from"] == "contradicts"
    attach_claims_to_tree(doc, sources=[DECL_ROW])
    assert _claim(doc, "p1")["verdict"] == VERIFIED, _claim(doc, "p1")["reason"]

    # A "different value" whose evidence figures the claim states identically is
    # refuted by the texts (live run 2026-09-27).
    text3 = "The collision deductible is $500 and the comprehensive deductible is $250."
    node3 = _decl_paragraph("p3", text3, "Comprehensive Deductible: $250")

    def hallucinated(claim: str, source: str) -> dict[str, Any]:
        return {
            "verdict": "contradicts",
            "reasoning": "The SOURCE does not mention the collision deductible, but the deductible for comprehensive coverage is different.",
            "evidence": "Comprehensive Deductible: $250",
            "model": "m",
            "checked_at": "t",
        }

    doc3 = _document(node3)
    attach_entailment_to_tree(doc3, checker=hallucinated)
    rec3 = doc3["body"][0]["children"][0]["meta"]["provenance"]["entailment"]
    assert rec3["verdict"] == "no"
    assert "states identically" in rec3["citations"][0]["reasoning"]

    # A real conflict, reasoned as one, stands.
    def conflict(claim: str, source: str) -> dict[str, Any]:
        return {
            "verdict": "contradicts",
            "reasoning": "The SOURCE states the collision deductible as $500, a different amount.",
            "evidence": source,
            "model": "m",
            "checked_at": "t",
        }

    text2 = "The collision deductible is $250."
    doc2 = _document(_decl_paragraph("p2", text2, "Collision Deductible: $500"))
    attach_entailment_to_tree(doc2, checker=conflict)
    assert doc2["body"][0]["children"][0]["meta"]["provenance"]["entailment"]["verdict"] == "contradicts"


def test_a_two_fact_sentence_with_a_label_line_and_an_initial_is_split() -> None:
    text = (
        "policy snapshot\nThe named insured is John Q Sample, according to sentence, and the policy "
        "period is from 01/15/2025 to 01/15/2026, as stated in sentence."
    )
    node = _decl_paragraph("p1", text, "Named Insured: John Q. Sample")
    node["provenance"].append({**node["provenance"][0], "extracted_quote": "Policy Period: 01/15/2025 to 01/15/2026"})
    doc = _document(node)
    _judge(doc, {text: "no"}, sources=[DECL_ROW])
    block = _claim(doc, "p1")
    assert block["checks"]["kind"] == "enumeration"
    # "John Q Sample" against a source that writes "John Q. Sample": the period
    # after an initial is orthography (the compile's sentence map drops it), so
    # the name is found and both facts verify.
    assert [sc["status"] for sc in block["checks"]["sub_claims"]] == ["verified", "verified"]
    assert block["verdict"] == VERIFIED, block["reason"]


def test_verbatim_ignores_the_period_after_an_initial_and_nothing_else() -> None:
    from prompt_matrix.services.claim_policy import quote_is_verbatim

    assert quote_is_verbatim("Named Insured: John Q Sample", "Named Insured: John Q. Sample")
    assert quote_is_verbatim("Named Insured: John Q. Sample", "Named  Insured:\nJohn Q Sample")
    assert not quote_is_verbatim("Named Insured: John Sample", "Named Insured: John Q. Sample")
    assert not quote_is_verbatim("Total Premium: $1250.00", "Total Premium: $1,250.00")
    assert not quote_is_verbatim("named insured: john q. sample", "Named Insured: John Q. Sample")


def test_a_two_fact_sentence_is_assessed_per_fact() -> None:
    """Two facts cited to two one-line windows: each window lacks the other fact,
    so a per-window entailment says ``no`` twice. The facts are checked one by one
    against the whole source instead (live run 2026-09-27)."""
    text = "policy snapshot\nThe named insured is John Q. Sample, and the policy period is from 01/15/2025 to 01/15/2026."
    node = _decl_paragraph("p1", text, "Named Insured: John Q. Sample")
    node["provenance"].append({**node["provenance"][0], "extracted_quote": "Policy Period: 01/15/2025 to 01/15/2026"})
    doc = _document(node)
    _judge(doc, {text: "no"}, sources=[DECL_ROW])
    block = _claim(doc, "p1")
    assert block["checks"]["kind"] == "enumeration"
    assert block["verdict"] == VERIFIED, block["reason"]
    assert len(block["checks"]["sub_claims"]) == 2


def test_a_calculation_is_never_split_into_sub_claims() -> None:
    text = "The premium rose from $1,000 to $1,200, an increase of 20%."
    doc = _document(_paragraph("p1", text, "Prior premium $1,000; renewal premium $1,200."))
    _judge(doc, {text: "yes"}, sources=[{**ROW, "extracted_text": ROW["extracted_text"] + " Prior premium $1,000; renewal premium $1,200."}])
    block = _claim(doc, "p1")
    assert block["checks"]["kind"] == "fact"
    assert block["verdict"] == VERIFIED, block["reason"]
    assert block["checks"]["numeric"]["status"] == "recomputed_ok"


def test_a_document_fact_is_not_mistaken_for_a_meta_statement() -> None:
    text = "Flood damage is excluded under this policy."
    doc = _document(_decl_paragraph("p1", text, "Flood damage is excluded under this policy."))
    _judge(doc, {text: "yes"}, sources=[DECL_ROW])
    assert _claim(doc, "p1")["checks"]["kind"] == "fact"
    assert _claim(doc, "p1")["verdict"] == VERIFIED


# --------------------------------------------------------------------------- #
# Eligibility
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "eligible"),
    [
        ("Flood is excluded.", True),
        ("Deductible $25,000.", True),
        ("Effective 01/15/2025.", True),
        ("Fully compliant.", True),
        ("Coverage summary", False),
        ("Limits apply.", False),
        ("The policy liability limit is set at five million.", True),
    ],
)
def test_short_paragraphs_with_a_figure_exclusion_or_risk_term_are_claims(text: str, eligible: bool) -> None:
    assert is_claim_eligible(text) is eligible


def test_a_short_exclusion_sentence_is_counted_and_judged() -> None:
    text = "Flood is excluded."
    doc = _document(_paragraph("p1", text, "Flood is excluded."))
    _judge(doc, {text: "yes"})
    counts = _provenance_counts(doc)
    assert counts["eligible"] == 1
    assert counts["verified"] == 1


# --------------------------------------------------------------------------- #
# Gate / ok truth table and the summary
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("z3", "kw", "expected"),
    [
        ("VIOLATION", {}, "blocked"),
        ("VIOLATION", {"contradicted": 1}, "blocked"),
        ("PASS", {"contradicted": 1}, "review"),
        ("PASS", {"unsupported_count": 1}, "review"),
        ("PASS", {"insufficient": 1}, "review"),
        ("PASS", {"flagged": 1}, "review"),
        ("PASS", {"verified": 0}, "review"),
        ("PASS", {"verified": 3}, "pass"),
        ("SKIPPED", {"verified": 3}, "pass"),
        ("SKIPPED", {}, "review"),
        ("PASS", {}, "pass"),
    ],
)
def test_gate_truth_table(z3: str, kw: dict, expected: str) -> None:
    assert compute_gate_status(z3, 0, **kw) == expected
    assert compute_gate_status(z3, 1, **kw) == ("blocked" if z3 == "VIOLATION" else "review")


def test_ok_only_when_every_claim_is_verified_without_flags() -> None:
    text_a = "The deductible is $25,000 per occurrence."
    text_b = "Flood is excluded."
    doc = _document(
        _paragraph("a", text_a, "The deductible is $25,000 per occurrence."),
        _paragraph("b", text_b, "Flood is excluded."),
    )
    _judge(doc, {text_a: "yes", text_b: "yes"})
    fields = provenance_gate_fields(document=doc, z3_status="PASS", redhat_count=0, has_substrate=True, sources=[ROW])
    assert fields["ok"] is True and fields["gate_status"] == "pass"
    assert fields["claim_summary"] == {
        "total": 2, "verified": 2, "unsupported": 0, "contradicted": 0,
        "insufficient": 0, "flagged": 0, "paragraphs": 2, "meta": 0,
        "policy": CLAIM_POLICY_ID, "inconsistencies": [],
    }
    assert doc["meta"]["claim_summary"] == fields["claim_summary"]
    assert fields["provenance_stats"]["supported"] == fields["provenance_stats"]["verified"] == 2

    # One contradiction: review, with the count named.
    doc2 = _document(
        _paragraph("a", text_a, "The deductible is $25,000 per occurrence."),
        _paragraph("b", "Flood is covered.", "Flood is excluded."),
    )
    _judge(doc2, {text_a: "yes", "Flood is covered.": "contradicts"})
    fields2 = provenance_gate_fields(document=doc2, z3_status="PASS", redhat_count=0, has_substrate=True, sources=[ROW])
    assert fields2["ok"] is False and fields2["gate_status"] == "review"
    assert "1 claims contradicted by their source" in fields2["unverified_reason"]
    assert fields2["claim_summary"]["contradicted"] == 1
    assert fields2["claim_summary"]["flagged"] == 1  # "covered" not in the quote

    # Verified but flagged: still review, ok False.
    text_c = "Coverage is guaranteed for vacant properties."
    doc3 = _document(_paragraph("c", text_c, "Vacant properties exceeding 60 consecutive days require referral to the underwriter before coverage applies."))
    _judge(doc3, {text_c: "yes"})
    fields3 = provenance_gate_fields(document=doc3, z3_status="PASS", redhat_count=0, has_substrate=True, sources=[ROW])
    assert fields3["claim_summary"]["verified"] == 1
    assert fields3["ok"] is False and fields3["gate_status"] == "review"
    assert "flagged" in fields3["unverified_reason"]

    # Z3 violation blocks whatever the claims say.
    fields4 = provenance_gate_fields(document=doc, z3_status="VIOLATION", redhat_count=0, has_substrate=True, sources=[ROW])
    assert fields4["ok"] is False and fields4["gate_status"] == "blocked"


def test_inconsistent_figures_across_claims_are_flagged_not_reverdicted() -> None:
    # Same qualified label ("annual premium") with two values; a bare "premium"
    # would be a different label and would not be compared.
    text_a = "The annual premium is $1,250.00."
    text_b = "The annual premium of $1,500 is due at inception."
    doc = _document(
        _paragraph("a", text_a, "The annual premium is $1,250.00 and the policy fee is $250."),
        _paragraph("b", text_b, "The annual premium is $1,250.00 and the policy fee is $250."),
    )
    _judge(doc, {text_a: "yes", text_b: "no"})
    fields = provenance_gate_fields(document=doc, z3_status="PASS", redhat_count=0, has_substrate=True, sources=[ROW])
    assert _claim(doc, "a")["verdict"] == VERIFIED
    assert "inconsistent_figure:annual premium" in _claim(doc, "a")["flags"]
    assert "inconsistent_figure:annual premium" in _claim(doc, "b")["flags"]
    assert fields["claim_summary"]["inconsistencies"] == [
        {"label": "annual premium", "kind": "money", "values": ["1250", "1500"], "node_ids": ["a", "b"]}
    ]
    assert fields["claim_summary"]["flagged"] == 2


def test_the_summary_payload_carries_the_claim_summary_and_keeps_the_block() -> None:
    text = "The deductible is $25,000 per occurrence."
    doc = _document(_paragraph("p1", text, "The deductible is $25,000 per occurrence."))
    _judge(doc, {text: "yes"})
    summary = build_audit_summary(
        z3_results={"status": "PASS", "violations": [], "lock_results": []},
        redhat_critiques=[],
        document=doc,
        has_substrate=True,
        sources=[ROW],
    )
    assert summary["claim_summary"]["verified"] == 1
    assert summary["ok"] is True
    node = summary["document"]["body"][0]["children"][0]
    # The provenance rebuild inside build_audit_summary must not lose the block.
    assert node["meta"]["provenance"]["claim"]["verdict"] == VERIFIED
    assert node["meta"]["provenance"]["entailment"]["verdict"] == "yes"
    assert summary["document"]["meta"]["claim_summary"] == summary["claim_summary"]


def test_derive_claim_honours_injected_checks() -> None:
    node = _paragraph("p1", "The deductible is $25,000 per occurrence.", "The deductible is $25,000 per occurrence.")
    block = derive_claim(
        node,
        sources=[ROW],
        carry_plan=None,
        entailment="yes",
        numeric={"status": "not_applicable", "detail": "injected", "expected": None, "stated": None},
        wording={"flags": [], "unsupported_terms": []},
    )
    assert block["verdict"] == VERIFIED
    assert block["checks"]["numeric"]["detail"] == "injected"
    assert node["meta"]["provenance"]["claim"] is block


# --------------------------------------------------------------------------- #
# The compile cache key re-verifies under a new rule
# --------------------------------------------------------------------------- #


def test_compile_cache_key_moves_with_the_entailment_prompt_version(monkeypatch) -> None:
    from prompt_matrix.routers import draft

    monkeypatch.setenv("ASSURE_FROZEN_PROJECTS", "")
    before = draft._compile_cache_key("proj-1", "Restate the limit.", None, "ctx", "model/x")
    monkeypatch.setattr(draft, "ENTAILMENT_PROMPT_VERSION", draft.ENTAILMENT_PROMPT_VERSION + 1)
    after = draft._compile_cache_key("proj-1", "Restate the limit.", None, "ctx", "model/x")
    assert before != after
    assert f"e{draft.ENTAILMENT_PROMPT_VERSION}" in draft._prompt_key_material("proj-1")
    assert CLAIM_POLICY_ID in draft._prompt_key_material("proj-1")


def test_entailment_cache_key_moves_with_the_prompt_version(monkeypatch) -> None:
    from prompt_matrix.services import entailment
    from prompt_matrix.services.entailment_cache import verdict_cache_key

    prompt = entailment.build_entailment_prompt("c", "s")
    k1 = verdict_cache_key("c", "s", f"v{entailment.ENTAILMENT_PROMPT_VERSION}\n{prompt}", "m")
    k2 = verdict_cache_key("c", "s", f"v{entailment.ENTAILMENT_PROMPT_VERSION + 1}\n{prompt}", "m")
    assert k1 != k2


def test_recount_of_a_cached_compile_reads_the_claim_blocks(monkeypatch) -> None:
    """The replay path re-derives from the claim layer against the ask's sources."""
    from prompt_matrix.routers.draft import _recount_cached_verified

    text = "The deductible is $25,000 per occurrence."
    doc = _document(_paragraph("p1", text, "The deductible is $25,000 per occurrence."))
    attach_entailment_to_tree(doc, checker=_stub({text: "yes"}))
    cached = {"verified": {"document": doc, "z3_status": "PASS", "redhat_count": 0, "unverified": True, "unverified_reason": "stale"}}
    payload = _recount_cached_verified(cached, has_substrate=True, sources=[ROW])
    assert payload["claim_summary"]["verified"] == 1
    assert payload["ok"] is True
    assert "unverified" not in payload
    # Without sources nothing can be verified: the recount says so, not "pass".
    doc2 = _document(_paragraph("p1", text, "The deductible is $25,000 per occurrence."))
    attach_entailment_to_tree(doc2, checker=_stub({text: "yes"}))
    payload2 = _recount_cached_verified({"verified": {"document": doc2, "z3_status": "PASS"}}, has_substrate=True)
    assert payload2["claim_summary"]["insufficient"] == 1
    assert payload2["ok"] is False
