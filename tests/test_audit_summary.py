"""Tests for unified audit summary helper."""

from __future__ import annotations

import pytest

from prompt_matrix.models.jdf import attach_substrate_provenance_to_tree, build_document_from_draft
from prompt_matrix.services.audit_summary import (
    build_audit_summary,
    compute_gate_status,
    normalize_audit_payload,
)
from prompt_matrix.services.entailment import attach_entailment_to_tree


#: A source long enough to clear claim-v1's 200-character floor, carrying the
#: sentence the verdict-counter fixtures quote. The claim policy (2026-09-27)
#: verifies nothing without the source text: a VERIFIED claim needs its quote
#: verbatim in a supplied source, so the fixtures below now hand one in.
FILING_TEXT = (
    "ANNUAL REPORT. Management discussion and analysis. Net income grew twelve "
    "percent in fiscal 2025. Operating expenses were held flat across the year and "
    "the board declared no special dividend. Liquidity remained adequate for the "
    "planned capital programme through fiscal 2026."
)
FILING_ROWS = [{"id": "sub-filing", "filename": "policy.pdf", "extracted_text": FILING_TEXT}]


def _yes_checker(claim: str, source: str) -> dict:
    """Stands in for the SEMANTIC_VALIDATION model: these claims are entailed.

    The prompt's own discrimination is tested against the live model in
    tests/test_entailment.py; here the transport is irrelevant to the gate.
    """
    return {
        "verdict": "yes",
        "reasoning": "The source states the claim.",
        "model": "stub/model",
        "checked_at": "2026-09-18T00:00:00+00:00",
    }


def test_compute_gate_status_blocked():
    assert compute_gate_status("VIOLATION", 0) == "blocked"


def test_compute_gate_status_review():
    assert compute_gate_status("PASS", 2) == "review"


def test_compute_gate_status_pass():
    assert compute_gate_status("PASS", 0) == "pass"


def test_compute_gate_status_never_passes_with_a_contradicted_claim():
    assert compute_gate_status("PASS", 0, unsupported_count=1) == "review"
    assert compute_gate_status("VIOLATION", 0, unsupported_count=1) == "blocked"
    assert compute_gate_status("SKIPPED", 0, unsupported_count=1) == "review"


def test_gate_holds_when_supported_claims_sit_beside_contradicted_ones():
    """1 verified + 5 contradicted, Z3 PASS, no Red-Hat finding.

    Before 2026-09-23 `provenance_gate_fields` returned early on
    `counts["supported"] > 0` and the gate read `pass` / `ok: True` — the five
    paragraphs the source denies went out as verified.

    Updated 2026-09-27 for claim-v1: a denied claim is entailment ``contradicts``
    (``no`` now means "not stated"), the count lives in ``contradicted`` and the
    reason names it; the fixture supplies the source so the yes-claim can be
    VERIFIED against a verbatim quote.
    """
    from tests.test_verdict_counters import _document, _paragraph

    quote = "Net income grew twelve percent in fiscal 2025."
    doc = _document(
        _paragraph("p-yes", quotes=[quote], verdict="yes"),
        *[_paragraph(f"p-no-{i}", quotes=[quote], verdict="contradicts") for i in range(5)],
    )
    summary = build_audit_summary(
        z3_results=_passing_z3(),
        redhat_critiques=[],
        document=doc,
        has_substrate=True,
        sources=FILING_ROWS,
    )
    assert summary["provenance_stats"]["supported"] == 1
    assert summary["provenance_stats"]["verified"] == 1
    assert summary["provenance_stats"]["contradicted"] == 5
    assert summary["provenance_stats"]["unsupported"] == 0
    assert summary["claim_summary"]["contradicted"] == 5
    assert summary["gate_status"] == "review"
    assert summary["ok"] is False
    assert summary["unverified"] is True
    assert summary["unverified_reason"] == (
        "1 of 6 claims verified (5 claims contradicted by their source)."
    )


def test_normalize_audit_payload_reads_unsupported_from_the_stats():
    out = normalize_audit_payload(
        {
            "z3_results": {"status": "PASS"},
            "redhat_critiques": [],
            "provenance_stats": {"eligible": 2, "supported": 1, "unsupported": 1},
        }
    )
    assert out["gate_status"] == "review"
    assert out["ok"] is False


def test_build_audit_summary_shape():
    z3 = {"status": "PASS", "violations": [], "lock_results": [], "locks_verified": 1}
    redhat = [{"title": "Red-hat review", "content": "ok", "model": "bedrock/us.anthropic.claude-sonnet-5-5"}]
    summary = build_audit_summary(
        z3_results=z3,
        redhat_critiques=redhat,
        nodes=[{"id": "p-1", "type": "paragraph", "content": "Hi"}],
        locks=[{"canonical_key": "Revenue", "value": 100}],
    )
    assert summary["ok"] is False
    # New contract: ok is True only when at least one paragraph is
    # provenance-anchored. An unanchored document returns 'review'.
    assert summary["ok"] is False
    assert summary["gate_status"] == "review"
    assert summary["unverified"] is True
    assert summary["provenance_stats"] == {
        "eligible": 0,
        "anchored": 0,
        "supported": 0,
        "partial": 0,
        "unsupported": 0,
        "unanchored": 0,
        "unverified": 0,
        # claim-v1 buckets (2026-09-27).
        "verified": 0,
        "contradicted": 0,
        "insufficient": 0,
        "flagged": 0,
        "meta": 0,
    }
    assert summary["claim_summary"]["total"] == 0
    assert summary["gate_status"] == "review"
    assert summary["z3_status"] == "PASS"
    assert summary["redhat_count"] == 1
    assert summary["redhat_critiques"] == redhat
    assert summary["redhat_results"] == redhat
    assert summary["node_count"] == 1
    assert summary["lock_count"] == 1


def test_normalize_audit_payload_legacy_redhat_results():
    payload = normalize_audit_payload(
        {
            "z3_results": {"status": "VIOLATION", "violations": ["x"]},
            "redhat_results": [{"title": "t", "content": "c"}],
        }
    )
    assert payload["gate_status"] == "blocked"
    assert payload["ok"] is False
    assert payload["redhat_count"] == 1
    assert payload["redhat_critiques"][0]["title"] == "t"


# ---------------------------------------------------------------------------
# Both directions of the grounding gate against the demo fixture. The shell shows
# the ungrounded banner iff provenance_stats.anchored == 0, so `anchored` and
# `unverified_reason` are the customer-visible contract.
# ---------------------------------------------------------------------------


def _policy_rows() -> list[dict]:
    from pathlib import Path

    from pypdf import PdfReader

    pdf = Path(__file__).parent / "fixtures" / "policy-sample.pdf"
    source = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)
    return [{"id": "sub-policy", "filename": "policy-sample.pdf", "extracted_text": source}]


def _policy_document(text: str) -> dict:
    tree = build_document_from_draft("p1", text).model_dump(mode="json")
    return attach_substrate_provenance_to_tree(tree, [], _policy_rows())


def _passing_z3() -> dict:
    return {"status": "PASS", "violations": [], "lock_results": []}


@pytest.mark.parametrize(
    "claim",
    [
        "The policy establishes a combined single limit of $5,000,000 for liability coverage.",
        # The minimal demo document: one paragraph quoting the source verbatim.
        "The policy liability limit is set at $5,000,000 for combined single limit.",
    ],
)
def test_gate_anchors_policy_restatement(claim):
    doc = _policy_document(claim)
    # The anchor itself is lexical — that is `anchored`; the entailment check
    # said yes to this claim against the quote it anchored to. Two layers, two
    # numbers.
    attach_entailment_to_tree(doc, checker=_yes_checker)
    # claim-v1 (2026-09-27): VERIFIED needs the quote verbatim in a supplied
    # source of sufficient quality. The fixture PDF carries 144 characters of
    # text — under the 200-character source floor — so the honest verdict here is
    # INSUFFICIENT_EVIDENCE with the measurement as its basis, not a pass. The
    # anchor and the entailment are unchanged (anchored 1, no unverified,
    # no unsupported); what changed is that a stub of a source cannot verify.
    summary = build_audit_summary(
        z3_results=_passing_z3(),
        redhat_critiques=[],
        document=doc,
        has_substrate=True,
        sources=_policy_rows(),
    )
    assert summary["provenance_stats"] == {
        "eligible": 1,
        "anchored": 1,
        "supported": 0,
        "partial": 0,
        "unsupported": 0,
        "unanchored": 0,
        "unverified": 0,
        "verified": 0,
        "contradicted": 0,
        "insufficient": 1,
        "flagged": 0,
        "meta": 0,
    }
    block = summary["document"]["body"][0]["children"][0]["meta"]["provenance"]["claim"]
    assert block["quote_verbatim"] is True
    assert block["checks"]["source_quality"] == {
        "status": "low",
        "basis": "the source carries 144 characters of text (under 200)",
    }
    assert block["page"] is None, "the fixture has no page layout; a page is never defaulted"
    assert summary["unverified_reason"] == "0 of 1 claims verified (1 with insufficient evidence)."
    assert summary["gate_status"] == "review"
    assert summary["ok"] is False


@pytest.mark.parametrize(
    "claim",
    [
        "The policy includes $250,000 of cyber coverage for the insured's network operations.",
        "Cyber coverage of $250,000 applies to network operations.",
    ],
)
def test_gate_refuses_unsupported_claim(claim):
    doc = _policy_document(claim)
    summary = build_audit_summary(
        z3_results=_passing_z3(),
        redhat_critiques=[],
        document=doc,
        has_substrate=True,
        sources=_policy_rows(),
    )
    # claim-v1 (2026-09-27): an unanchored claim is UNSUPPORTED ("no source
    # sentence carries this claim"), so `unsupported` is 1 where it used to be 0.
    assert summary["provenance_stats"] == {
        "eligible": 1,
        "anchored": 0,
        "supported": 0,
        "partial": 0,
        "unsupported": 1,
        "unanchored": 1,
        "unverified": 0,
        "verified": 0,
        "contradicted": 0,
        "insufficient": 0,
        "flagged": 0,
        "meta": 0,
    }
    assert summary["unverified"] is True
    assert summary["unverified_reason"] == "0 of 1 claims matched any source sentence."
    assert summary["gate_status"] == "review"
    assert summary["ok"] is False
