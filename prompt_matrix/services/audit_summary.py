"""Unified audit summary for sandbox verify and draft audit_complete SSE."""

from __future__ import annotations

from typing import Any, Literal

GateStatus = Literal["pass", "blocked", "review"]


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
    node_count: int | None = None,
    lock_count: int | None = None,
) -> dict[str, Any]:
    """Canonical audit payload shared by sandbox verify and draft audit_complete."""
    z3_status = str(z3_results.get("status") or "SKIPPED")
    redhat_count = len(redhat_critiques)
    ok = z3_status == "PASS"
    gate_status = compute_gate_status(z3_status, redhat_count)

    summary: dict[str, Any] = {
        "ok": ok,
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
    if document is not None:
        summary["document"] = document
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
