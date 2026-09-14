"""Denormalize node.meta.provenance for the Decision Provenance panel."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

try:
    from ..ledger.z3_ledger import check_claim
except ImportError:
    from ledger.z3_ledger import check_claim

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _rule_for_node(
    node: dict[str, Any],
    ledger: dict[str, Any],
    z3_results: dict[str, Any] | None,
) -> str:
    content = str(node.get("content") or node.get("title") or "")
    numbers = [float(n) for n in _NUM.findall(content)]
    lock_results = (z3_results or {}).get("lock_results") or []
    for item in lock_results:
        if not item.get("ok"):
            continue
        key = str(item.get("key") or "")
        try:
            val = float(item.get("value"))
        except (TypeError, ValueError):
            continue
        if numbers and any(abs(val - n) < 1e-9 for n in numbers):
            return f"{key} == {val}"
        if key and key.lower() in content.lower():
            return f"{key} == {val}"
    for key, raw in (ledger or {}).items():
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if numbers and any(abs(val - n) < 1e-9 for n in numbers):
            return f"{key} == {val}"
    ann = (node.get("annotations") or {}).get("z3") or []
    for item in ann:
        ck = str(item.get("canonical_key") or "")
        if ck:
            return f"{ck} constraint"
    return "ledger_check"


def _best_confidence(node: dict[str, Any]) -> float:
    meta = node.get("meta") or {}
    spans = meta.get("confidenceSpans") or []
    if spans:
        return max(float(s.get("score") or 0) for s in spans if isinstance(s, dict))
    return 0.92


def _provenance_row(node: dict[str, Any]) -> dict[str, Any] | None:
    rows = node.get("provenance") or []
    for row in rows:
        if isinstance(row, dict) and (row.get("source_name") or row.get("source_id")):
            return row
    return None


def build_node_provenance_meta(
    node: dict[str, Any],
    *,
    ledger: dict[str, Any] | None = None,
    z3_results: dict[str, Any] | None = None,
    verified_at: str | None = None,
) -> dict[str, Any] | None:
    """UI-ready provenance summary for one node."""
    content = str(node.get("content") or node.get("title") or "").strip()
    if not content:
        return None
    row = _provenance_row(node)
    if not row:
        return None
    checked = check_claim(
        content[:500],
        context=content,
        ledger=ledger if isinstance(ledger, dict) else {},
        source_label=str(row.get("source_name") or ""),
        source_id=str(row.get("source_id") or ""),
    )
    prov = dict(checked.get("provenance") or {})
    if row:
        prov.setdefault("source_id", str(row.get("source_id") or ""))
        prov.setdefault("source_name", str(row.get("source_name") or ""))
        page = row.get("page_number")
        if page not in (None, ""):
            try:
                prov["page_number"] = int(page)
            except (TypeError, ValueError):
                prov["page_number"] = page
        quote = str(row.get("extracted_quote") or "").strip()
        if quote:
            prov.setdefault("excerpt", quote)
    prov.setdefault("rule", _rule_for_node(node, ledger or {}, z3_results))
    prov.setdefault("confidence", _best_confidence(node))
    prov.setdefault("verified_at", verified_at or datetime.now(UTC).isoformat())
    if not prov.get("excerpt"):
        prov["excerpt"] = content[:240]
    return prov


def _walk_nodes(document: dict[str, Any]) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    for section in document.get("body") or []:
        if isinstance(section, dict):
            ordered.append(section)
            for child in section.get("children") or []:
                if isinstance(child, dict):
                    ordered.append(child)
    return ordered


def attach_provenance_meta_to_tree(
    document: dict[str, Any],
    *,
    z3_results: dict[str, Any] | None = None,
    verified_at: str | None = None,
) -> dict[str, Any]:
    """Write ``node.meta.provenance`` on every paragraph-like node."""
    if not document:
        return document
    ledger = document.get("truth_ledger") or {}
    ts = verified_at or datetime.now(UTC).isoformat()
    for node in _walk_nodes(document):
        ntype = str(node.get("type") or "")
        if ntype not in ("paragraph", "callout", "section"):
            continue
        summary = build_node_provenance_meta(
            node,
            ledger=ledger if isinstance(ledger, dict) else {},
            z3_results=z3_results,
            verified_at=ts,
        )
        if not summary:
            continue
        meta = dict(node.get("meta") or {})
        meta["provenance"] = summary
        node["meta"] = meta
    return document
