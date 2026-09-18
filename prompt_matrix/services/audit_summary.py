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


def _provenance_counts(document: dict[str, Any]) -> dict[str, int]:
    """Claim-eligible paragraphs, bucketed by what the entailment check said.

    ``anchored`` keeps its name and meaning ("verified against a source") and is
    now strictly ``entailment.verdict == "yes"`` — the lexical anchor alone no
    longer counts. ``no``, a missing verdict, and ``unverified`` all sit in
    ``unanchored``; ``partial`` is its own bucket; ``unverified`` (a call that
    could not be made or parsed) is also counted separately so a broken check is
    visible instead of silently reading as "no claim matched". ``unchecked`` is
    not part of the reported shape: it only distinguishes "anchored but never
    entailed" from "matched no source at all" in the reason text.
    """
    counts = {
        "eligible": 0,
        "anchored": 0,
        "partial": 0,
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
        verdict = _entailment_verdict(node)
        if verdict == "yes":
            counts["anchored"] += 1
        elif verdict == "partial":
            counts["partial"] += 1
            counts["unanchored"] += 1
        else:
            counts["unanchored"] += 1
            if verdict == "unverified":
                counts["unverified"] += 1
            elif node.get("provenance"):
                counts["unchecked"] += 1
    return counts


def _eligible_and_anchored(document: dict[str, Any]) -> tuple[int, int]:
    """Count paragraph nodes (>= _MIN_CLAIM_TOKENS content tokens) and how many
    are anchored.

    Same claim floor as the substrate matcher in models/jdf.py, so a paragraph the
    gate counts is a paragraph the matcher was allowed to anchor. Headers/stubs are
    excluded. Anchored means the anchored source sentence *entails* the claim
    (``entailment.verdict == "yes"``) — see ``_provenance_counts``.
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
    gate_status = compute_gate_status(z3_status, redhat_count)

    summary: dict[str, Any] = {
        "ok": False,
        "gate_status": gate_status,
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

    eligible = 0
    anchored = 0
    partial = 0
    unverified_claims = 0
    unchecked = 0
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
        counts = _provenance_counts(document)
        eligible = counts["eligible"]
        anchored = counts["anchored"]
        partial = counts["partial"]
        unverified_claims = counts["unverified"]
        unchecked = counts["unchecked"]

    summary["provenance_stats"] = {
        "eligible": eligible,
        "anchored": anchored,
        "partial": partial,
        "unanchored": eligible - anchored - partial,
        "unverified": unverified_claims,
    }

    if anchored > 0:
        # Gate unchanged from Z3 + Red-Hat once at least one claim is anchored.
        summary["ok"] = z3_status == "PASS"
    else:
        # A document with no entailment-verified claim is unverified, not "pass".
        if document is None:
            reason = "No document to inspect."
        elif partial or unverified_claims:
            bits = []
            if partial:
                bits.append(f"{partial} supported only in part")
            if unverified_claims:
                bits.append(f"{unverified_claims} could not be checked")
            reason = (
                f"0 of {eligible} claims were entailed by their matched source sentence "
                f"({', '.join(bits)})."
            )
        elif unchecked:
            reason = (
                f"0 of {eligible} claims were entailment-checked against their matched "
                f"source sentence ({unchecked} anchored but never checked)."
            )
        elif has_substrate is True:
            reason = f"0 of {eligible} claims matched any source sentence."
        elif has_substrate is False:
            reason = "No sources included in this compile — output is ungrounded."
        elif eligible == 0:
            reason = "No sources uploaded — compile is ungrounded."
        else:
            reason = f"0 of {eligible} claims matched any source sentence."
        summary["gate_status"] = "review"
        summary["ok"] = False
        summary["unverified"] = True
        summary["unverified_reason"] = reason
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
