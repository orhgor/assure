"""Entailment verdicts: parsing, persistence, and what the gate counts.

The anchoring these verdicts sit on is lexical (``models/jdf.py``), so these tests
use a stub checker: the transport is not under test, the contract is.
"""

from __future__ import annotations

from typing import Any

import pytest

from prompt_matrix.services.audit_summary import build_audit_summary
from prompt_matrix.services.entailment import (
    attach_entailment_to_tree,
    parse_entailment_verdict,
    unverified,
)

SCHEMA_KEYS = {"verdict", "reasoning", "model", "checked_at"}


def _stub(verdicts: dict[str, str], calls: list[tuple[str, str]] | None = None):
    def check(claim: str, source: str) -> dict[str, Any]:
        if calls is not None:
            calls.append((claim, source))
        verdict = verdicts.get(claim)
        if verdict is None:
            raise AssertionError(f"unexpected claim: {claim}")
        return {
            "verdict": verdict,
            "reasoning": f"{verdict} because reasons",
            "model": "stub/model",
            "checked_at": "2026-09-18T00:00:00+00:00",
        }

    return check


def _paragraph(node_id: str, content: str, quote: str) -> dict[str, Any]:
    provenance = []
    if quote is not None:
        provenance = [
            {
                "source_type": "internal_doc",
                "source_name": "policy.pdf",
                "source_id": f"sub-{node_id}",
                "page_number": "1",
                "extracted_quote": quote,
                "url_or_doi": "",
                "accessed_date": "",
            }
        ]
    return {
        "type": "paragraph",
        "id": node_id,
        "content": content,
        "entities_referenced": [],
        "provenance": provenance,
        "meta": {},
        "annotations": {"redhat": [], "z3": []},
    }


def _document(*paragraphs: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": "doc-entailment",
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


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("VERDICT: yes\nREASON: The source states the figure.", "yes"),
        ("VERDICT: no\nREASON: The source carries a different figure.", "no"),
        ("verdict: partial\nreason: The source omits the broker.", "partial"),
        ("**VERDICT:** Yes\n**REASON:** Identical sentences.", "yes"),
    ],
)
def test_parse_reads_each_verdict(raw: str, expected: str) -> None:
    record = parse_entailment_verdict(raw, "z-ai/glm-5.3-flash")
    assert record["verdict"] == expected
    assert record["reasoning"]
    assert record["model"] == "z-ai/glm-5.3-flash"
    assert record["checked_at"]


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "ERROR: litellm.AuthenticationError: no api key",
        "I am not sure, it depends on the context.",
    ],
)
def test_parse_never_invents_a_verdict(raw: str) -> None:
    """An unparseable answer is 'unverified', never a default yes/no."""
    assert parse_entailment_verdict(raw, "z-ai/glm-5.3-flash")["verdict"] == "unverified"


def test_unverified_reason_carries_failure_and_hides_credentials() -> None:
    record = unverified("ERROR: 401 from Bearer sk-abcdef123456 rejected")
    assert record["verdict"] == "unverified"
    assert "sk-abcdef123456" not in record["reasoning"]
    assert "401" in record["reasoning"]


# ---------------------------------------------------------------------------
# Persistence on the tree
# ---------------------------------------------------------------------------


def test_attach_persists_frozen_schema_and_caches_within_compile() -> None:
    calls: list[tuple[str, str]] = []
    claim = "The policy limit is five million dollars for liability."
    doc = _document(
        _paragraph("p1", claim, "The policy limit is five million dollars for liability."),
        # Same claim/quote pair twice: one call, not two, inside a single compile.
        _paragraph("p2", claim, "The policy limit is five million dollars for liability."),
    )
    attach_entailment_to_tree(doc, checker=_stub({claim: "yes"}, calls))

    assert calls == [(claim, "The policy limit is five million dollars for liability.")]
    for node in doc["body"][0]["children"]:
        record = node["meta"]["provenance"]["entailment"]
        assert set(record) == SCHEMA_KEYS
        assert record["verdict"] == "yes"


def test_attach_marks_a_failed_call_unverified_with_its_reason() -> None:
    def exploding(claim: str, source: str) -> dict[str, Any]:
        raise RuntimeError("entailment transport down")

    doc = _document(_paragraph("p1", "The limit is five million dollars.", "The limit is five million dollars."))
    attach_entailment_to_tree(doc, checker=exploding)
    record = doc["body"][0]["children"][0]["meta"]["provenance"]["entailment"]
    assert record["verdict"] == "unverified"
    assert "entailment transport down" in record["reasoning"]


def test_attach_marks_a_garbage_checker_answer_unverified() -> None:
    doc = _document(_paragraph("p1", "The limit is five million dollars.", "The limit is five million dollars."))
    attach_entailment_to_tree(doc, checker=lambda claim, source: {"verdict": "maybe"})
    record = doc["body"][0]["children"][0]["meta"]["provenance"]["entailment"]
    assert record["verdict"] == "unverified"


def test_attach_skips_nodes_without_a_source_quote() -> None:
    """A node with no extracted quote must not be checked against its own text."""
    calls: list[tuple[str, str]] = []
    doc = _document(_paragraph("p1", "The limit is five million dollars.", ""))
    attach_entailment_to_tree(doc, checker=_stub({}, calls))
    assert calls == []
    assert "entailment" not in (doc["body"][0]["children"][0]["meta"].get("provenance") or {})


def test_attach_checks_a_two_sentence_anchor_against_the_whole_window() -> None:
    """The check reads the window the matcher cleared its floors against.

    A paragraph anchored across two source sentences asks a question the single
    quoted sentence cannot answer — it carries half the claim — and the model then
    reports a supported claim as partial. The window is what the anchor rests on,
    so it is what the check is given.
    """
    claim = (
        "Physical inspections are required every 24 months and properties vacant "
        "over 60 consecutive days trigger a referral."
    )
    sentence = "Physical inspection of occupied commercial properties is required at least once every 24 months"
    window = (
        sentence + " Vacant properties exceeding 60 consecutive days require referral"
    )
    calls: list[tuple[str, str]] = []
    node = _paragraph("p1", claim, sentence)
    node["provenance"][0]["anchor_window"] = window
    attach_entailment_to_tree(_document(node), checker=_stub({claim: "yes"}, calls))
    assert calls == [(claim, window)]


def test_attach_falls_back_to_the_quote_when_a_row_carries_no_window() -> None:
    """Rows written before the window existed are still checked, on their quote."""
    claim = "Physical inspections are required every 24 months."
    sentence = "Physical inspection of occupied commercial properties is required at least once every 24 months"
    calls: list[tuple[str, str]] = []
    attach_entailment_to_tree(
        _document(_paragraph("p1", claim, sentence)), checker=_stub({claim: "yes"}, calls)
    )
    assert calls == [(claim, sentence)]


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def _four_verdict_document() -> dict[str, Any]:
    """One paragraph per verdict path, each lexically anchored to the same quote."""
    claims = {
        "yes": "Net income grew twelve percent in fiscal 2025.",
        "no": "Net income declined twelve percent in fiscal 2025.",
        "partial": "Net income grew twelve percent in fiscal 2025 across all regions.",
        "unverified": "Net income grew twelve percent in fiscal 2025 per the filing.",
    }
    doc = _document(
        *[
            _paragraph(
                f"p-{verdict}", claim, "Net income grew twelve percent in fiscal 2025."
            )
            for verdict, claim in claims.items()
        ]
    )
    attach_entailment_to_tree(doc, checker=_stub({claim: v for v, claim in claims.items()}))
    return doc


def test_gate_counts_only_entailed_claims() -> None:
    """Four verdict paths plus a lexical-only anchor, through the real summary."""
    doc = _four_verdict_document()
    # A fifth paragraph that is lexically anchored but never entailment-checked:
    # exactly what the old gate counted as "verified".
    doc["body"][0]["children"].append(
        _paragraph(
            "p-lexical",
            "Net income grew twelve percent in fiscal 2025 as reported.",
            "Net income grew twelve percent in fiscal 2025.",
        )
    )

    summary = build_audit_summary(
        z3_results={"status": "PASS", "violations": [], "lock_results": []},
        redhat_critiques=[],
        document=doc,
        has_substrate=True,
    )

    # Grounding: all five paragraphs carry the quote they were anchored to.
    # Verdicts: one yes, one partial, one no, one unverified, one never checked
    # (`p-lexical`). `anchored` no longer stands in for the verdict.
    assert summary["provenance_stats"] == {
        "eligible": 5,
        "anchored": 5,
        "supported": 1,
        "partial": 1,
        "unsupported": 1,
        "unanchored": 0,
        "unverified": 1,
    }
    # One entailed claim still unlocks the Z3 gate (unchanged rule); the other
    # buckets are reported, not hidden.
    assert summary["gate_status"] == "pass"
    assert summary["ok"] is True


def test_gate_names_partial_and_unverified_when_nothing_is_entailed() -> None:
    """A broken or partial check must read differently from 'no source matched'."""
    doc = _four_verdict_document()
    doc["body"][0]["children"] = [
        node for node in doc["body"][0]["children"] if node["id"] != "p-yes"
    ]

    summary = build_audit_summary(
        z3_results={"status": "PASS", "violations": [], "lock_results": []},
        redhat_critiques=[],
        document=doc,
        has_substrate=True,
    )

    assert summary["provenance_stats"] == {
        "eligible": 3,
        "anchored": 3,
        "supported": 0,
        "partial": 1,
        "unsupported": 1,
        "unanchored": 0,
        "unverified": 1,
    }
    assert summary["gate_status"] == "review"
    assert summary["ok"] is False
    assert summary["unverified"] is True
    assert summary["unverified_reason"] == (
        "0 of 3 claims were entailed by their matched source sentence "
        "(1 supported only in part, 1 contradicted by their source, "
        "1 could not be checked)."
    )


def test_gate_passes_only_on_an_entailed_claim() -> None:
    claim = "The policy limit is five million dollars for liability."
    doc = _document(
        _paragraph("p1", claim, "The policy limit is five million dollars for liability.")
    )
    attach_entailment_to_tree(doc, checker=_stub({claim: "yes"}))
    summary = build_audit_summary(
        z3_results={"status": "PASS", "violations": [], "lock_results": []},
        redhat_critiques=[],
        document=doc,
        has_substrate=True,
    )
    assert summary["provenance_stats"]["anchored"] == 1
    assert summary["provenance_stats"]["unanchored"] == 0
    assert summary["ok"] is True
    assert not summary.get("unverified")


def test_gate_treats_a_lexical_anchor_as_unverified() -> None:
    """The real policy fixture: lexical anchoring happens, entailment does not.

    This is the behaviour change — the gate used to read this document as
    verified on token overlap alone. `anchored` reports the anchor it has (1);
    `supported` reports what the missing verdict is worth (0).
    """
    from tests.test_audit_summary import _passing_z3, _policy_document

    doc = _policy_document(
        "The policy liability limit is set at $5,000,000 for combined single limit."
    )
    node = doc["body"][0]["children"][0]
    assert node["provenance"], "fixture must anchor lexically for this test to mean anything"

    summary = build_audit_summary(
        z3_results=_passing_z3(), redhat_critiques=[], document=doc, has_substrate=True
    )
    assert summary["provenance_stats"] == {
        "eligible": 1,
        "anchored": 1,
        "supported": 0,
        "partial": 0,
        "unsupported": 0,
        "unanchored": 0,
        "unverified": 0,
    }
    assert summary["unverified_reason"] == (
        "0 of 1 claims were entailment-checked against their matched source sentence "
        "(1 anchored but never checked)."
    )
    assert summary["ok"] is False
    assert summary["gate_status"] == "review"


def test_entailment_survives_the_summary_provenance_rebuild() -> None:
    claim = "The policy limit is five million dollars for liability."
    doc = _document(
        _paragraph("p1", claim, "The policy limit is five million dollars for liability.")
    )
    attach_entailment_to_tree(doc, checker=_stub({claim: "yes"}))
    summary = build_audit_summary(
        z3_results={"status": "PASS", "violations": [], "lock_results": []},
        redhat_critiques=[],
        document=doc,
    )
    prov = summary["document"]["body"][0]["children"][0]["meta"]["provenance"]
    assert prov["entailment"]["verdict"] == "yes"
    assert prov["excerpt"] == "The policy limit is five million dollars for liability."
