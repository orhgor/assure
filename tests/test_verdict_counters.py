"""Per-citation verdicts, the aggregate, and the numbers the product reports.

Three layers, each of which was wrong at least once:

* ``entailment._claim_sources`` — every source sentence a paragraph cites, not the
  first (a paragraph citing three is supported by the three together).
* ``entailment._aggregate_verdicts`` / ``_contradicted`` — one verdict per
  paragraph, plus the separate fact that a citation of it was contradicted.
* ``audit_summary._provenance_counts`` — the buckets. ``supported`` counts a
  ``yes`` OR a ``partial``; ``unsupported`` counts a ``no`` verdict OR any
  contradicted citation, so ``[yes, no]`` appears in BOTH — the paragraph is
  carried by some of what it cites and simultaneously contains a claim its own
  source contradicts. Counting only the verdict left the ``no`` unreported
  everywhere; counting only ``yes`` as supported reported a renewal memo whose
  every paragraph is carried as ``supported 0``.
"""

from __future__ import annotations

from typing import Any

import pytest

from prompt_matrix.services.audit_summary import (
    _provenance_counts,
    _reported_stats,
    provenance_gate_fields,
)
from prompt_matrix.services.entailment import (
    _aggregate_verdicts,
    _claim_sources,
    _contradicted,
    attach_entailment_to_tree,
)

CLAIM = "The policy liability limit is five million dollars per occurrence."


def _paragraph(
    node_id: str,
    *,
    content: str = CLAIM,
    quotes: list[str] | None = None,
    verdict: str | None = None,
    contradicted: bool | None = None,
    window: str = "",
) -> dict[str, Any]:
    provenance = []
    for quote in quotes or []:
        row: dict[str, Any] = {
            "source_type": "internal_doc",
            "source_name": "policy.pdf",
            "source_id": f"sub-{node_id}",
            "page_number": "1",
            "extracted_quote": quote,
            "url_or_doi": "",
            "accessed_date": "",
        }
        if window:
            row["anchor_window"] = window
        provenance.append(row)
    meta: dict[str, Any] = {}
    if verdict is not None:
        record: dict[str, Any] = {"verdict": verdict, "reasoning": "r"}
        if contradicted is not None:
            record["contradicted"] = contradicted
        meta["provenance"] = {"entailment": record}
    return {
        "type": "paragraph",
        "id": node_id,
        "content": content,
        "entities_referenced": [],
        "provenance": provenance,
        "meta": meta,
    }


def _document(*nodes: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": "doc-verdicts",
        "meta": {},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "sec-1",
                "title": "Coverage",
                "children": list(nodes),
                "meta": {},
            }
        ],
    }


# --------------------------------------------------------------------------- #
# Item 6 — the aggregate
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("verdicts", "expected_verdict", "expected_contradicted"),
    [
        (["yes", "yes"], "yes", False),
        (["yes", "partial"], "partial", False),
        (["yes", "no"], "partial", True),
        (["no", "no"], "no", True),
        (["partial", "no"], "partial", True),
        (["unverified"], "unverified", False),
        (["yes", "unverified"], "partial", False),
        ([], "unverified", False),
    ],
)
def test_the_aggregate_of_a_citations_verdicts(
    verdicts: list[str], expected_verdict: str, expected_contradicted: bool
) -> None:
    assert _aggregate_verdicts(verdicts) == expected_verdict
    assert _contradicted(verdicts) is expected_contradicted


def test_an_empty_verdict_list_is_not_a_pass():
    """``all([])`` is vacuously True: the empty list must not read as supported."""
    assert _aggregate_verdicts([]) == "unverified"
    assert _contradicted([]) is False


def test_every_citation_of_a_paragraph_is_judged_not_only_the_first():
    """A paragraph citing three sentences is checked against the three together.

    Judging the first one only reports a weaker claim than the source carries —
    and the aggregate then rests on a citation the paragraph may not even open
    with.
    """
    quotes = ["The limit is five million dollars.", "It applies per occurrence.", "It is combined single limit."]
    seen: list[str] = []

    def checker(_claim: str, source: str) -> dict[str, Any]:
        seen.append(source)
        return {"verdict": "yes", "reasoning": "ok"}

    doc = _document(_paragraph("p1", quotes=quotes))
    attach_entailment_to_tree(doc, checker=checker)
    assert seen == quotes
    record = doc["body"][0]["children"][0]["meta"]["provenance"]["entailment"]
    assert record["verdict"] == "yes"
    assert [c["source"] for c in record["citations"]] == quotes


def test_a_repeated_evidence_window_is_judged_once():
    """Two rows quoting the same sentence are one question, not two calls."""
    seen: list[str] = []

    def checker(_claim: str, source: str) -> dict[str, Any]:
        seen.append(source)
        return {"verdict": "yes", "reasoning": "ok"}

    doc = _document(_paragraph("p1", quotes=["The limit is five million dollars."] * 2))
    attach_entailment_to_tree(doc, checker=checker)
    assert seen == ["The limit is five million dollars."]


def test_the_evidence_is_the_window_when_the_row_carries_one():
    """A row's ``anchor_window`` is the run the matcher scored — the whole of it."""
    node = _paragraph(
        "p1",
        quotes=["The limit is five million dollars."],
        window="The limit is five million dollars. It applies per occurrence.",
    )
    assert _claim_sources(node) == [
        "The limit is five million dollars. It applies per occurrence."
    ]


def test_a_paragraph_with_no_provenance_is_never_judged():
    def checker(_claim: str, _source: str) -> dict[str, Any]:
        raise AssertionError("nothing to check")

    doc = _document(_paragraph("p1"))
    attach_entailment_to_tree(doc, checker=checker)
    meta = doc["body"][0]["children"][0]["meta"]
    assert "entailment" not in (meta.get("provenance") or {})


# --------------------------------------------------------------------------- #
# Item 7 — the counters
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        (["yes", "yes"], {"supported": 1, "partial": 0, "unsupported": 0, "unverified": 0}),
        (["yes", "partial"], {"supported": 1, "partial": 1, "unsupported": 0, "unverified": 0}),
        (["yes", "no"], {"supported": 1, "partial": 1, "unsupported": 1, "unverified": 0}),
        (["no", "no"], {"supported": 0, "partial": 0, "unsupported": 1, "unverified": 0}),
        (["partial", "no"], {"supported": 1, "partial": 1, "unsupported": 1, "unverified": 0}),
        (["unverified"], {"supported": 0, "partial": 0, "unsupported": 0, "unverified": 1}),
        # One citation verifies and another could not be checked: the paragraph
        # aggregates to ``partial``, so the unverified citation leaves no counter
        # of its own — the per-citation state is visible only in the record's
        # ``citations``. Reported in the run notes as a gap, not fixed here.
        (["yes", "unverified"], {"supported": 1, "partial": 1, "unsupported": 0, "unverified": 0}),
        ([], {"supported": 0, "partial": 0, "unsupported": 0, "unverified": 1}),
    ],
)
def test_every_combination_lands_in_the_right_buckets(
    verdicts: list[str], expected: dict[str, int]
) -> None:
    """The whole matrix, through the aggregate, the record and the counters.

    ``[yes, no]`` is the load-bearing row: supported 1 AND unsupported 1. A
    paragraph carried by one citation and contradicted by another is both, and the
    contradiction is the number the reader needs.
    """
    doc = _document(
        _paragraph(
            "p1",
            quotes=[
                f"The limit is five million dollars ({n})." for n in range(max(1, len(verdicts)))
            ],
        )
    )
    node = doc["body"][0]["children"][0]
    if verdicts:
        node["meta"]["provenance"] = {
            "entailment": {
                "verdict": _aggregate_verdicts(verdicts),
                "contradicted": _contradicted(verdicts),
                "reasoning": "stubbed",
            }
        }
    else:
        # No per-citation verdict was produced for a paragraph that carries one:
        # the aggregate the record would hold is the empty-list verdict.
        node["meta"]["provenance"] = {
            "entailment": {"verdict": _aggregate_verdicts([]), "reasoning": "stubbed"}
        }

    counts = _provenance_counts(doc)
    assert counts["eligible"] == 1
    assert counts["anchored"] == 1
    assert counts["unanchored"] == 0
    for bucket, expected_value in expected.items():
        assert counts[bucket] == expected_value, (verdicts, bucket, counts)
    # The reported projection is the same numbers, and nothing unreported leaks in.
    assert _reported_stats(counts) == {
        "eligible": 1,
        "anchored": 1,
        "supported": expected["supported"],
        "partial": expected["partial"],
        "unsupported": expected["unsupported"],
        "unanchored": 0,
        "unverified": expected["unverified"],
    }


def test_a_contradiction_reaches_the_reported_reason():
    """The gate's own sentence names the contradicted claim.

    A tree with no supported claim and one contradicted citation reports the
    contradiction as its reason; if the counter misses it, the reason reads "0 of
    N claims matched any source sentence", which is a different (and false) story.
    """
    doc = _document(_paragraph("p1", quotes=["Something else entirely."], verdict="no", contradicted=True))
    fields = provenance_gate_fields(
        document=doc, z3_status="SKIPPED", redhat_count=0, has_substrate=True
    )
    assert fields["provenance_stats"]["unsupported"] == 1
    assert fields["gate_status"] == "review"
    assert fields["unverified"] is True
    assert "contradicted by their source" in fields["unverified_reason"]


def test_a_partly_carried_paragraph_is_supported_and_reported_as_partial():
    doc = _document(
        _paragraph("p1", quotes=["The limit is five million dollars."], verdict="partial", contradicted=False)
    )
    counts = _provenance_counts(doc)
    assert counts["supported"] == 1
    assert counts["partial"] == 1
    assert counts["unsupported"] == 0
    fields = provenance_gate_fields(
        document=doc, z3_status="PASS", redhat_count=0, has_substrate=True
    )
    # Supported, so nothing is left to report as unverified.
    assert fields["provenance_stats"]["supported"] == 1
    assert "unverified_reason" not in fields


def test_an_anchored_paragraph_with_no_verdict_is_unchecked_not_unsupported():
    """Anchored but never checked is its own state: not a pass, not a refusal."""
    doc = _document(_paragraph("p1", quotes=["The limit is five million dollars."]))
    counts = _provenance_counts(doc)
    assert counts["anchored"] == 1
    assert counts["unchecked"] == 1
    assert counts["supported"] == 0
    assert counts["unsupported"] == 0
    # ``unchecked`` is a detail of the reason text, never a reported number.
    assert "unchecked" not in _reported_stats(counts)


def test_a_short_paragraph_is_not_a_claim():
    """Below the claim floor a paragraph is not counted, anchored or not."""
    doc = _document(_paragraph("p1", content="Limits apply.", quotes=["Limits apply."], verdict="yes"))
    counts = _provenance_counts(doc)
    assert counts["eligible"] == 0
    assert counts["anchored"] == 0


def test_an_unanchored_paragraph_is_counted_apart_from_an_unsupported_one():
    doc = _document(_paragraph("p1"), _paragraph("p2", content=CLAIM + " It applies per occurrence."))
    counts = _provenance_counts(doc)
    assert counts["eligible"] == 2
    assert counts["anchored"] == 0
    assert counts["unanchored"] == 2
    assert counts["unsupported"] == 0


def test_the_record_carries_the_judgement_it_was_given():
    """``model`` and ``checked_at`` reach the node, and a failure leads the reason.

    The record's frozen shape is ``{verdict, reasoning, model, checked_at}``. An
    aggregate that dropped two of the four read as the contract while carrying half
    of it, and put the caller's failure reason only inside ``citations`` — where the
    Evidence pane, which shows ``reasoning``, never looks. For a paragraph citing
    two sentences, the reason has to name what went wrong before it summarizes.
    """
    quotes = ["The limit is five million dollars.", "It applies per occurrence."]

    def checker(_claim: str, source: str) -> dict[str, Any]:
        if "per occurrence" in source:
            raise RuntimeError("entailment transport down")
        return {
            "verdict": "yes",
            "reasoning": "The source states the limit.",
            "model": "stub/checker",
            "checked_at": "2026-09-18T00:00:00+00:00",
        }

    doc = _document(_paragraph("p1", quotes=quotes))
    attach_entailment_to_tree(doc, checker=checker)
    record = doc["body"][0]["children"][0]["meta"]["provenance"]["entailment"]
    assert record["verdict"] == "partial"
    assert record["contradicted"] is False
    assert "entailment transport down" in record["reasoning"]
    assert "2 citation(s)" in record["reasoning"]
    # The latest judgement behind the paragraph: ISO 8601 in one format sorts
    # chronologically, so the newest check is what the node reports.
    assert record["checked_at"] == max(c["checked_at"] for c in record["citations"])
    assert record["checked_at"]
    assert record["model"] == "stub/checker"
    assert [c["verdict"] for c in record["citations"]] == ["yes", "unverified"]
