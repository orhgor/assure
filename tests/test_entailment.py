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

#: The record the node carries. ``contradicted`` is deliberate and newer than the
#: rest: a ``[yes, no]`` paragraph is carried in part and simultaneously contains a
#: citation its own source contradicts, and the aggregate verdict alone cannot say
#: so. The other four are the contract the module documents — an aggregate that
#: dropped ``model`` and ``checked_at`` read as the frozen shape while carrying
#: neither, and left the failure reason only inside ``citations``.
SCHEMA_KEYS = {
    "verdict",
    "contradicted",
    "reasoning",
    "model",
    "checked_at",
    "citations",
    # 2026-09-27: the prompt version the verdict was judged under (four labels).
    "prompt_version",
}

#: A source long enough to clear claim-v1's 200-character floor, carrying the
#: sentence the gate fixtures quote. Since 2026-09-27 nothing is VERIFIED without
#: its quote verbatim in a supplied source, so the gate tests hand this in.
FILING_TEXT = (
    "ANNUAL REPORT. Management discussion and analysis. Net income grew twelve "
    "percent in fiscal 2025. The policy limit is five million dollars for liability. "
    "Operating expenses were held flat across the year and the board declared no "
    "special dividend. Liquidity remained adequate through fiscal 2026."
)
FILING_ROWS = [{"id": "sub-filing", "filename": "policy.pdf", "extracted_text": FILING_TEXT}]


def _stub(verdicts: dict[str, str], calls: list[tuple[str, str]] | None = None):
    def check(claim: str, source: str) -> dict[str, Any]:
        if calls is not None:
            calls.append((claim, source))
        verdict = verdicts.get(claim)
        if verdict is None:
            raise AssertionError(f"unexpected claim: {claim}")
        record = {
            "verdict": verdict,
            "reasoning": f"{verdict} because reasons",
            "model": "stub/model",
            "checked_at": "2026-09-18T00:00:00+00:00",
        }
        if verdict == "contradicts":
            # A contradiction stands only on conflicting source text found
            # verbatim in the window (2026-09-27); the stub quotes the window.
            record["evidence"] = source
        return record

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
        ("VERDICT: no\nREASON: The source does not carry the figure.", "no"),
        ("VERDICT: contradicts\nREASON: The source carries a different figure.", "contradicts"),
        ("**VERDICT:** Contradicted\n**REASON:** The source says excluded.", "contradicts"),
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
    """One paragraph per verdict path, each lexically anchored to the same quote.

    Five since 2026-09-27: ``contradicts`` ("the source says otherwise") is split
    out of ``no`` ("not stated").
    """
    claims = {
        "yes": "Net income grew twelve percent in fiscal 2025.",
        "no": "Net income grew twelve percent in fiscal 2025 in the Americas segment.",
        "contradicts": "Net income declined twelve percent in fiscal 2025.",
        "partial": "Net income grew twelve percent in fiscal 2025 across every region.",
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
        sources=FILING_ROWS,
    )

    # Grounding: all six paragraphs carry the quote they were anchored to.
    # Verdicts: one yes, one partial, one no, one contradicts, one unverified, one
    # never checked (`p-lexical`). Under claim-v1 (2026-09-27) `supported` equals
    # `verified` — only the yes-claim with its verbatim quote; `partial` is
    # UNSUPPORTED ("a material qualifier is missing"), `no` is UNSUPPORTED,
    # `contradicts` is CONTRADICTED, and a claim the verifier did not answer
    # (`unverified`) or never checked (`p-lexical`) is INSUFFICIENT_EVIDENCE.
    # `partial` / `unverified` stay as the entailment layer's own detail.
    assert summary["provenance_stats"] == {
        "eligible": 6,
        "anchored": 6,
        "supported": 1,
        "partial": 1,
        "unsupported": 2,
        "unanchored": 0,
        "unverified": 1,
        "verified": 1,
        "contradicted": 1,
        "insufficient": 2,
        "flagged": 0,
        "meta": 0,
    }
    # The contradicted paragraph holds the gate: a verified claim does not
    # outvote one the source denies (until 2026-09-23 it did, and this
    # document read `pass` / `ok: True`). The counts are named, so the reader is
    # not sent looking for a missing source when the source is there and
    # disagrees.
    assert summary["gate_status"] == "review"
    assert summary["ok"] is False
    assert summary["unverified_reason"] == (
        "1 of 6 claims verified (1 claims contradicted by their source, 2 unsupported, "
        "2 with insufficient evidence, 1 anchored but never checked)."
    )


def test_gate_names_contradicted_and_unverified_when_nothing_is_supported() -> None:
    """A denied claim and a broken check must read differently from 'no source matched'."""
    doc = _four_verdict_document()
    doc["body"][0]["children"] = [
        node
        for node in doc["body"][0]["children"]
        if node["id"] not in ("p-yes", "p-partial")
    ]

    summary = build_audit_summary(
        z3_results={"status": "PASS", "violations": [], "lock_results": []},
        redhat_critiques=[],
        document=doc,
        has_substrate=True,
        sources=FILING_ROWS,
    )

    # claim-v1 (2026-09-27): `no` (not stated) is UNSUPPORTED, `contradicts` is
    # CONTRADICTED, `unverified` is INSUFFICIENT_EVIDENCE — three states, three
    # numbers, and the reason names each.
    assert summary["provenance_stats"] == {
        "eligible": 3,
        "anchored": 3,
        "supported": 0,
        "partial": 0,
        "unsupported": 1,
        "unanchored": 0,
        "unverified": 1,
        "verified": 0,
        "contradicted": 1,
        "insufficient": 1,
        "flagged": 0,
        "meta": 0,
    }
    assert summary["gate_status"] == "review"
    assert summary["ok"] is False
    assert summary["unverified"] is True
    assert summary["unverified_reason"] == (
        "0 of 3 claims verified (1 claims contradicted by their source, 1 unsupported, "
        "1 with insufficient evidence)."
    )


def test_gate_treats_a_partly_carried_claim_as_unsupported() -> None:
    """A verdict of `partial` is NOT verified (rule change, 2026-09-27).

    `partial` is what the auditor answers for a claim whose cited sentences carry
    its central assertion but not a material element it also asserts. Until
    2026-09-27 that counted as `supported` and earned the gate; under the
    customer's claim policy a claim with a missing qualifier is UNSUPPORTED
    ("a material qualifier is missing") and holds the gate at review. `partial`
    is still reported as the entailment layer's own detail bucket.
    """
    doc = _four_verdict_document()
    doc["body"][0]["children"] = [
        node for node in doc["body"][0]["children"] if node["id"] == "p-partial"
    ]

    summary = build_audit_summary(
        z3_results={"status": "PASS", "violations": [], "lock_results": []},
        redhat_critiques=[],
        document=doc,
        has_substrate=True,
        sources=FILING_ROWS,
    )

    assert summary["provenance_stats"] == {
        "eligible": 1,
        "anchored": 1,
        "supported": 0,
        "partial": 1,
        "unsupported": 1,
        "unanchored": 0,
        "unverified": 0,
        "verified": 0,
        "contradicted": 0,
        "insufficient": 0,
        "flagged": 0,
        "meta": 0,
    }
    node = doc["body"][0]["children"][0]
    assert node["meta"]["provenance"]["claim"]["reason"] == "a material qualifier is missing"
    assert summary["gate_status"] == "review"
    assert summary["ok"] is False
    assert summary["unverified"] is True
    assert summary["unverified_reason"] == "0 of 1 claims verified (1 unsupported)."


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
        sources=FILING_ROWS,
    )
    assert summary["provenance_stats"]["anchored"] == 1
    assert summary["provenance_stats"]["unanchored"] == 0
    assert summary["provenance_stats"]["verified"] == 1
    assert summary["ok"] is True
    assert not summary.get("unverified")


def test_gate_treats_a_lexical_anchor_as_unverified() -> None:
    """The real policy fixture: lexical anchoring happens, entailment does not.

    This is the behaviour change — the gate used to read this document as
    verified on token overlap alone. `anchored` reports the anchor it has (1);
    `supported` reports what the missing verdict is worth (0).
    """
    from tests.test_audit_summary import _passing_z3, _policy_document, _policy_rows

    doc = _policy_document(
        "The policy liability limit is set at $5,000,000 for combined single limit."
    )
    node = doc["body"][0]["children"][0]
    assert node["provenance"], "fixture must anchor lexically for this test to mean anything"

    summary = build_audit_summary(
        z3_results=_passing_z3(),
        redhat_critiques=[],
        document=doc,
        has_substrate=True,
        sources=_policy_rows(),
    )
    # claim-v1 (2026-09-27): anchored but never entailment-checked is
    # INSUFFICIENT_EVIDENCE ("no entailment check ran for this claim").
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
    assert summary["unverified_reason"] == (
        "0 of 1 claims verified (1 with insufficient evidence, 1 anchored but never checked)."
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


def test_contradiction_needs_visible_opposition_in_the_evidence():
    """Live 2026-09-27: a compound sentence was called ``contradicts`` against a
    verbatim window that merely lacked its second clause."""
    from prompt_matrix.services.entailment import enforce_contradiction_evidence

    source = "Flood damage is excluded under this policy. Agent: Mary Agent."
    claim = "The policy excludes flood damage, and the agent is Mary Agent."
    rec = {"verdict": "contradicts", "evidence": "Flood damage is excluded under this policy", "reasoning": "the source conflicts with the claim about the agent"}
    out = enforce_contradiction_evidence(dict(rec), source, claim)
    assert out["verdict"] == "no" and out["downgraded_from"] == "contradicts" and "no negation flip" in out["reasoning"]
    # A real flip stands: the claim says covered, the source says excluded.
    flipped = {"verdict": "contradicts", "evidence": "Flood damage is excluded under this policy", "reasoning": "the source says flood is excluded, the claim says it is covered"}
    out2 = enforce_contradiction_evidence(dict(flipped), source, "Flood damage is covered under this policy.")
    assert out2["verdict"] == "contradicts"
