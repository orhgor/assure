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
    """1 supported + 5 contradicted, Z3 PASS, no Red-Hat finding.

    Before 2026-09-23 `provenance_gate_fields` returned early on
    `counts["supported"] > 0` and the gate read `pass` / `ok: True` — the five
    paragraphs the source denies went out as verified.
    """
    from tests.test_verdict_counters import _document, _paragraph

    quote = "Net income grew twelve percent in fiscal 2025."
    doc = _document(
        _paragraph("p-yes", quotes=[quote], verdict="yes"),
        *[_paragraph(f"p-no-{i}", quotes=[quote], verdict="no") for i in range(5)],
    )
    summary = build_audit_summary(
        z3_results=_passing_z3(), redhat_critiques=[], document=doc, has_substrate=True
    )
    assert summary["provenance_stats"]["supported"] == 1
    assert summary["provenance_stats"]["unsupported"] == 5
    assert summary["gate_status"] == "review"
    assert summary["ok"] is False
    assert summary["unverified"] is True
    assert summary["unverified_reason"] == (
        "5 of 6 claims are contradicted by their source (1 entailed)."
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
    redhat = [{"title": "Red-hat review", "content": "ok", "model": "openrouter/qwen/qwen3-next-80b-a3b-instruct"}]
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
    }
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


def _policy_document(text: str) -> dict:
    from pathlib import Path

    from pypdf import PdfReader

    pdf = Path(__file__).parent / "fixtures" / "policy-sample.pdf"
    source = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)
    rows = [{"id": "sub-policy", "filename": "policy-sample.pdf", "extracted_text": source}]
    tree = build_document_from_draft("p1", text).model_dump(mode="json")
    return attach_substrate_provenance_to_tree(tree, [], rows)


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
    # The anchor itself is lexical — that is `anchored`; `supported` is the
    # entailment check that said yes to this claim against the quote it
    # anchored to. Two layers, two numbers.
    attach_entailment_to_tree(doc, checker=_yes_checker)
    summary = build_audit_summary(
        z3_results=_passing_z3(), redhat_critiques=[], document=doc, has_substrate=True
    )
    assert summary["provenance_stats"] == {
        "eligible": 1,
        "anchored": 1,
        "supported": 1,
        "partial": 0,
        "unsupported": 0,
        "unanchored": 0,
        "unverified": 0,
    }
    # Anchored branch leaves the unverified fields unset — the shell shows the
    # ungrounded banner only when anchored == 0.
    assert not summary.get("unverified")
    assert not summary.get("unverified_reason")
    assert summary["gate_status"] == "pass"
    assert summary["ok"] is True


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
        z3_results=_passing_z3(), redhat_critiques=[], document=doc, has_substrate=True
    )
    assert summary["provenance_stats"] == {
        "eligible": 1,
        "anchored": 0,
        "supported": 0,
        "partial": 0,
        "unsupported": 0,
        "unanchored": 1,
        "unverified": 0,
    }
    assert summary["unverified"] is True
    assert summary["unverified_reason"] == "0 of 1 claims matched any source sentence."
    assert summary["gate_status"] == "review"
    assert summary["ok"] is False
