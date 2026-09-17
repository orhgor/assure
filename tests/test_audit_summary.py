"""Tests for unified audit summary helper."""

from __future__ import annotations

import pytest

from prompt_matrix.models.jdf import attach_substrate_provenance_to_tree, build_document_from_draft
from prompt_matrix.services.audit_summary import (
    build_audit_summary,
    compute_gate_status,
    normalize_audit_payload,
)


def test_compute_gate_status_blocked():
    assert compute_gate_status("VIOLATION", 0) == "blocked"


def test_compute_gate_status_review():
    assert compute_gate_status("PASS", 2) == "review"


def test_compute_gate_status_pass():
    assert compute_gate_status("PASS", 0) == "pass"


def test_build_audit_summary_shape():
    z3 = {"status": "PASS", "violations": [], "lock_results": [], "locks_verified": 1}
    redhat = [{"title": "Red-hat review", "content": "ok", "model": "deepseek/deepseek-reasoner"}]
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
        "unanchored": 0,
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
    summary = build_audit_summary(
        z3_results=_passing_z3(), redhat_critiques=[], document=doc, has_substrate=True
    )
    assert summary["provenance_stats"] == {"eligible": 1, "anchored": 1, "unanchored": 0}
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
    assert summary["provenance_stats"] == {"eligible": 1, "anchored": 0, "unanchored": 1}
    assert summary["unverified"] is True
    assert summary["unverified_reason"] == "0 of 1 claims matched any source sentence."
    assert summary["gate_status"] == "review"
    assert summary["ok"] is False
