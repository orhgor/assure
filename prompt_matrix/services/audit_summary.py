"""Unified audit summary for sandbox verify and draft audit_complete SSE."""

from __future__ import annotations

from typing import Any, Literal

try:
    from .confidence_spans import (
        attach_confidence_spans_to_document,
        build_confidence_spans,
        build_macro_appendix,
    )
    from .provenance_meta import attach_provenance_meta_to_tree
    from ..models.jdf import _MIN_CLAIM_TOKENS, _tokenize
except ImportError:
    from services.confidence_spans import (
        attach_confidence_spans_to_document,
        build_confidence_spans,
        build_macro_appendix,
    )
    from services.provenance_meta import attach_provenance_meta_to_tree
    from models.jdf import _MIN_CLAIM_TOKENS, _tokenize

GateStatus = Literal["pass", "blocked", "review"]


def _walk_nodes(document: dict[str, Any]):
    for section in document.get("body") or []:
        if not isinstance(section, dict):
            continue
        yield section
        for child in section.get("children") or []:
            if isinstance(child, dict):
                yield child


def _entailment_verdict(node: dict[str, Any]) -> str:
    """``node.meta.provenance.entailment.verdict`` — "" when never checked."""
    meta = node.get("meta")
    prov = meta.get("provenance") if isinstance(meta, dict) else None
    if not isinstance(prov, dict):
        return ""
    record = prov.get("entailment")
    if not isinstance(record, dict):
        return ""
    return str(record.get("verdict") or "")


def _anchoring_quote(node: dict[str, Any]) -> str:
    """The source sentence this node is anchored to — "" when there is none.

    The quote the lexical matcher stamped (``models/jdf.py``); read from the
    node's provenance row, never from ``meta.provenance.excerpt``, which falls
    back to the claim text itself. The entailment check reads the same row's
    ``anchor_window`` — the run of sentences the anchor was scored against — so
    this is the sentence *cited*, not the whole evidence behind it.
    """
    for row in node.get("provenance") or []:
        if isinstance(row, dict) and str(row.get("extracted_quote") or "").strip():
            return str(row["extracted_quote"]).strip()
    return ""


def _provenance_counts(document: dict[str, Any]) -> dict[str, int]:
    """Claim-eligible paragraphs, bucketed by grounding and by entailment.

    The two layers are counted separately, because they answer different
    questions and used to be reported as one number:

    ``anchored``   the paragraph has a source sentence (a quote) — grounding.
    ``supported``  the entailment check read that sentence and said yes.
    ``partial``    it said the source supports the claim only in part.
    ``unsupported`` it said the source does not support the claim.
    ``unverified`` the call could not be made or parsed — visible, not "no".
    ``unchecked``  anchored but never checked; not reported, it only tells
                   "anchored but never entailed" from "matched no source".
    ``unanchored`` no quote at all: the matcher refused every candidate.

    Collapsing the first into the second (``anchored`` used to mean
    ``verdict == "yes"``) made a document whose paragraphs genuinely quote their
    sources read as "0 of 8 anchored" — and left no number for the grounding the
    document does have.
    """
    counts = {
        "eligible": 0,
        "anchored": 0,
        "supported": 0,
        "partial": 0,
        "unsupported": 0,
        "unanchored": 0,
        "unverified": 0,
        "unchecked": 0,
    }
    for node in _walk_nodes(document):
        if str(node.get("type") or "") != "paragraph":
            continue
        if len(_tokenize(str(node.get("content") or ""))) < _MIN_CLAIM_TOKENS:
            continue
        counts["eligible"] += 1
        if _anchoring_quote(node):
            counts["anchored"] += 1
        else:
            counts["unanchored"] += 1
        verdict = _entailment_verdict(node)
        # ``supported`` is the grounding number: the source carries the claim,
        # wholly or in part, and nothing in it contradicts. ``partial`` is the
        # detail bucket beside it. They answer different questions, the way
        # ``anchored`` and ``supported`` do — a synthesis paragraph that cites
        # three sentences is carried by them without any one of them stating
        # every element, and the entailment auditor says ``partial`` for exactly
        # that, correctly. Counting only ``yes`` reported a renewal memo whose
        # every paragraph is carried and none is contradicted as ``supported 0``.
        if verdict in ("yes", "partial"):
            counts["supported"] += 1
        if verdict == "partial":
            counts["partial"] += 1
        elif verdict == "no":
            counts["unsupported"] += 1
        elif verdict == "unverified":
            counts["unverified"] += 1
        elif node.get("provenance"):
            counts["unchecked"] += 1
    return counts


# The buckets a payload reports. `unchecked` is deliberately not among them: it
# only feeds the reason text, so it never reaches a surface as a number.
_PROVENANCE_REPORTED = (
    "eligible",
    "anchored",
    "supported",
    "partial",
    "unsupported",
    "unanchored",
    "unverified",
)


def _reported_stats(counts: dict[str, int]) -> dict[str, int]:
    """``_provenance_counts`` projected onto the reported buckets, in one shape.

    Every surface that reports provenance counters (the audit summary, the
    export gate, a replayed compile) builds them here, so a payload persisted or
    cached by an earlier compile cannot hand a surface numbers the current tree
    no longer supports.
    """
    return {key: int(counts.get(key) or 0) for key in _PROVENANCE_REPORTED}


def _eligible_and_anchored(document: dict[str, Any]) -> tuple[int, int]:
    """Count paragraph nodes (>= _MIN_CLAIM_TOKENS content tokens) and how many
    carry a source sentence.

    Same claim floor as the substrate matcher in models/jdf.py, so a paragraph the
    gate counts is a paragraph the matcher was allowed to anchor. Headers/stubs are
    excluded. Anchored means the paragraph has a matched source sentence (the
    matcher's quote) — its *truthfulness* is the separate verdict read from
    ``provenance_stats.supported``; see ``_provenance_counts``.
    """
    counts = _provenance_counts(document)
    return counts["eligible"], counts["anchored"]


def compute_gate_status(z3_status: str | None, redhat_count: int) -> GateStatus:
    """Map Z3 + Red-Hat counts to a single pre-flight gate state."""
    status = (z3_status or "").upper()
    if status == "VIOLATION":
        return "blocked"
    if redhat_count > 0:
        return "review"
    if status == "PASS":
        return "pass"
    return "review"


def provenance_gate_fields(
    *,
    document: dict[str, Any] | None,
    z3_status: str,
    redhat_count: int,
    has_substrate: bool | None,
) -> dict[str, Any]:
    """The provenance-derived fields of an audit payload: the stats and the gate.

    THE counter is ``_provenance_counts`` (projected by ``_reported_stats``), and
    this is the only writer of the stats and the verdict they drive — so a fresh
    compile, a compile replayed from cache and an export of a persisted gate all
    report the same numbers for the same tree.

    ``supported`` counts a verdict of ``yes`` OR ``partial``: ``partial`` means
    the anchored sentences carry the claim in part and nothing in them
    contradicts it, which is grounded. A synthesis paragraph citing three
    sentences is carried by them without any one of them stating every element,
    and a correct entailment auditor answers ``partial`` for exactly that;
    counting only ``yes`` reported a renewal memo whose every paragraph was
    carried and none contradicted as ``supported 0``.

    ``unverified`` is left unset when the recount found support: there is nothing
    to report as unverified, and a stale refusal must not outlive the numbers
    behind it.
    """
    counts = (
        _provenance_counts(document)
        if isinstance(document, dict)
        else {key: 0 for key in (*_PROVENANCE_REPORTED, "unchecked")}
    )
    fields: dict[str, Any] = {
        "provenance_stats": _reported_stats(counts),
        "gate_status": compute_gate_status(z3_status, redhat_count),
        "ok": z3_status == "PASS",
    }
    if counts["supported"] > 0:
        return fields

    # A document with no supported claim is unverified, not "pass" — however many
    # of its paragraphs quote a source. `partial` is not among the buckets below:
    # a partly carried claim already counts as supported, so a document holding
    # one never reaches this reason, and naming it here would be a bucket that
    # could not be filled.
    eligible = counts["eligible"]
    unsupported = counts["unsupported"]
    unverified_claims = counts["unverified"]
    if document is None:
        reason = "No document to inspect."
    elif unsupported or unverified_claims:
        bits = []
        if unsupported:
            bits.append(f"{unsupported} contradicted by their source")
        if unverified_claims:
            bits.append(f"{unverified_claims} could not be checked")
        reason = (
            f"0 of {eligible} claims were entailed by their matched source sentence "
            f"({', '.join(bits)})."
        )
    elif counts["unchecked"]:
        reason = (
            f"0 of {eligible} claims were entailment-checked against their matched "
            f"source sentence ({counts['unchecked']} anchored but never checked)."
        )
    elif has_substrate is True:
        reason = f"0 of {eligible} claims matched any source sentence."
    elif has_substrate is False:
        reason = "No sources included in this compile — output is ungrounded."
    elif eligible == 0:
        reason = "No sources uploaded — compile is ungrounded."
    else:
        reason = f"0 of {eligible} claims matched any source sentence."
    fields["gate_status"] = "review"
    fields["ok"] = False
    fields["unverified"] = True
    fields["unverified_reason"] = reason
    return fields


def build_audit_summary(
    *,
    z3_results: dict[str, Any],
    redhat_critiques: list[dict[str, Any]],
    nodes: list[Any] | None = None,
    locks: list[Any] | None = None,
    document: dict[str, Any] | None = None,
    has_substrate: bool | None = None,
    node_count: int | None = None,
    lock_count: int | None = None,
) -> dict[str, Any]:
    """Canonical audit payload shared by sandbox verify and draft audit_complete."""
    z3_status = str(z3_results.get("status") or "SKIPPED")
    redhat_count = len(redhat_critiques)

    summary: dict[str, Any] = {
        "ok": False,
        "gate_status": compute_gate_status(z3_status, redhat_count),
        "z3_status": z3_status,
        "z3_results": z3_results,
        "redhat_critiques": redhat_critiques,
        "redhat_count": redhat_count,
        "redhat_results": redhat_critiques,
    }
    if nodes is not None:
        summary["nodes"] = nodes
        summary["node_count"] = node_count if node_count is not None else len(nodes)
    if locks is not None:
        summary["locks"] = locks
        summary["lock_count"] = lock_count if lock_count is not None else len(locks)

    if document is not None:
        spans = build_confidence_spans(document, z3_results=z3_results)
        document = attach_confidence_spans_to_document(document, spans)
        document = attach_provenance_meta_to_tree(document, z3_results=z3_results)
        appendix = build_macro_appendix(
            document,
            z3_results=z3_results,
            redhat_critiques=redhat_critiques,
            confidence_spans=spans,
        )
        summary["document"] = document
        summary["confidenceSpans"] = spans
        summary["confidence_spans"] = spans
        summary["audit_manifest"] = appendix
        summary["claims"] = appendix

    # The provenance layer — `supported` (a claim its citations carry, in whole
    # or in part) and the gate verdict it drives — is written in exactly one
    # place, from the document just attached, so a replayed or exported payload
    # can be rewritten by the same rule. See `provenance_gate_fields`.
    summary.update(
        provenance_gate_fields(
            document=document,
            z3_status=z3_status,
            redhat_count=redhat_count,
            has_substrate=has_substrate,
        )
    )
    return summary


def normalize_audit_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize mixed field names from legacy clients or responses."""
    out = dict(data)
    critiques = out.get("redhat_critiques")
    if critiques is None:
        critiques = out.get("redhat_results") or []
    out["redhat_critiques"] = critiques
    out["redhat_results"] = critiques
    out["redhat_count"] = int(
        out.get("redhat_count") if out.get("redhat_count") is not None else len(critiques)
    )
    z3 = out.get("z3_results") or {}
    z3_status = out.get("z3_status") or z3.get("status")
    out["z3_status"] = z3_status
    if "gate_status" not in out:
        out["gate_status"] = compute_gate_status(
            str(z3_status) if z3_status else None, out["redhat_count"]
        )
    if "ok" not in out:
        out["ok"] = str(z3_status or "").upper() == "PASS"
    return out
