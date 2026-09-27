"""Denormalize node.meta.provenance for the Decision Provenance panel.

Evidence rules (2026-09-27, ``docs/evidence-honesty.md``): ``excerpt`` is the
anchored row's ``extracted_quote`` or the sentence ``ledger/z3_ledger`` located
in it — never the claim's own text (the old fallback presented the claim as its
source); ``confidence`` is always ``None`` (the old default was 0.92 for any
node with a provenance row, before any check ran); ``numeric_consistency`` is
the verdict word; ``verified_at`` is set only when a figure was checked against
a lock; ``rule`` names that lock or is ``None``.
"""

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
) -> str | None:
    """The lock a node's figure was checked against, or None when none was."""
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
    return None


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
    """UI-ready provenance summary for one node, or None without an anchored row.

    ``verified_at`` is the compile's timestamp only when a figure was checked
    against a lock (``numeric_consistency`` other than ``no_lock``); a node
    whose figures were not checked carries ``verified_at: None``.
    """
    content = str(node.get("content") or node.get("title") or "").strip()
    if not content:
        return None
    row = _provenance_row(node)
    if not row:
        return None
    quote = str(row.get("extracted_quote") or "").strip()
    window = str(row.get("anchor_window") or "").strip()
    # The check runs against the anchored source sentence (and the window it was
    # cut from), never against the claim's own content: a claim searched in
    # itself is always "found", and until 2026-09-27 that is what happened.
    checked = check_claim(
        content[:500],
        context=window or quote,
        ledger=ledger if isinstance(ledger, dict) else {},
        source_label=str(row.get("source_name") or ""),
        source_id=str(row.get("source_id") or ""),
    )
    prov = dict(checked.get("provenance") or {})
    prov["source_id"] = str(row.get("source_id") or "")
    prov["source_name"] = str(row.get("source_name") or "")
    page = row.get("page_number")
    if page in (None, ""):
        page = row.get("page")
    if page not in (None, ""):
        try:
            prov["page_number"] = int(page)
        except (TypeError, ValueError):
            prov["page_number"] = page
    else:
        prov["page_number"] = None
    # The cited sentence is the excerpt; the located sentence is only used when
    # the row carries no quote. No further fallback.
    prov["excerpt"] = quote or prov.get("excerpt") or None
    if not prov.get("rule"):
        prov["rule"] = _rule_for_node(node, ledger or {}, z3_results)
    prov["confidence"] = None
    prov["numeric_consistency"] = str(checked.get("numeric_consistency") or "no_lock")
    if prov["numeric_consistency"] == "no_lock":
        prov["verified_at"] = None
    elif verified_at:
        prov["verified_at"] = verified_at
    # The entailment verdict is a model judgement, not a re-derivable summary:
    # carry it across rebuilds (this function replaces meta.provenance wholesale,
    # and the audit gate reads the verdict from the tree it returns).
    prior = (node.get("meta") or {}).get("provenance") or {}
    entailment = prior.get("entailment") if isinstance(prior, dict) else None
    if isinstance(entailment, dict) and entailment.get("verdict"):
        prov["entailment"] = dict(entailment)
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
