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
    from ..models.jdf import _tokenize
except ImportError:
    from services.confidence_spans import (
        attach_confidence_spans_to_document,
        build_confidence_spans,
        build_macro_appendix,
    )
    from services.provenance_meta import attach_provenance_meta_to_tree
    from models.jdf import _tokenize

GateStatus = Literal["pass", "blocked", "review"]


_SOURCE_WORD_FLOOR = 8


def _walk_nodes(document: dict[str, Any]):
    for section in document.get("body") or []:
        if not isinstance(section, dict):
            continue
        yield section
        for child in section.get("children") or []:
            if isinstance(child, dict):
                yield child


def _eligible_and_anchored(document: dict[str, Any]) -> tuple[int, int]:
    """Count paragraph nodes (>=8 content tokens) and how many are anchored.

    Same eligibility floor as the substrate sentence matcher: paragraph-type
    nodes with >= 8 content tokens. Headers/stubs are excluded.
    """
    eligible = 0
    anchored = 0
    for node in _walk_nodes(document):
        if str(node.get("type") or "") != "paragraph":
            continue
        if len(_tokenize(str(node.get("content") or ""))) < _SOURCE_WORD_FLOOR:
            continue
        eligible += 1
        meta = node.get("meta") or {}
        if meta.get("provenance") or node.get("provenance"):
            anchored += 1
    return eligible, anchored


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
        eligible, anchored = _eligible_and_anchored(document)

    summary["provenance_stats"] = {
        "eligible": eligible,
        "anchored": anchored,
        "unanchored": eligible - anchored,
    }

    if anchored > 0:
        # Gate unchanged from Z3 + Red-Hat once at least one claim is anchored.
        summary["ok"] = z3_status == "PASS"
    else:
        # A document with zero provenance matches is unverified, not "pass".
        if document is None:
            reason = "No document to inspect."
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
