"""JDF AST delta engine for incremental Red-Hat audits."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Literal

try:
    from ..models.jdf import document_to_dict
except ImportError:
    from models.jdf import document_to_dict

ChangeKind = Literal["added", "modified", "deleted"]


def canonical_block_payload(node: dict[str, Any]) -> dict[str, Any]:
    """Audit-relevant block fields — excludes volatile annotations."""
    payload = copy.deepcopy(node)
    payload.pop("annotations", None)
    return payload


def hash_block(node: dict[str, Any]) -> str:
    """SHA-256 of canonical JDF block content for cache keys."""
    raw = json.dumps(canonical_block_payload(node), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _block_text(node: dict[str, Any]) -> str:
    ntype = str(node.get("type") or "")
    if ntype == "paragraph":
        return str(node.get("content") or "")
    if ntype == "callout":
        return f"{node.get('title') or ''}\n{node.get('content') or ''}".strip()
    if ntype == "table":
        parts = [str(node.get("caption") or "")]
        for row in node.get("rows") or []:
            if isinstance(row, list):
                parts.append(" | ".join(str(c) for c in row))
        return "\n".join(p for p in parts if p)
    if ntype == "signature":
        return str(node.get("content") or node.get("signer_name") or "")
    if ntype == "checkbox":
        return str(node.get("label") or "")
    if ntype == "image":
        return str(node.get("caption") or node.get("alt") or "")
    return json.dumps(canonical_block_payload(node), sort_keys=True)


def _iter_blocks(
    tree: dict[str, Any] | None,
) -> dict[str, tuple[dict[str, Any], dict[str, str]]]:
    """Map node_id -> (node, parent_context)."""
    doc = document_to_dict(tree or {})
    indexed: dict[str, tuple[dict[str, Any], dict[str, str]]] = {}
    for section in doc.get("body") or []:
        if not isinstance(section, dict):
            continue
        parent = {
            "section_id": str(section.get("id") or ""),
            "section_title": str(section.get("title") or ""),
        }
        for child in section.get("children") or []:
            if not isinstance(child, dict):
                continue
            node_id = str(child.get("id") or "").strip()
            if node_id:
                indexed[node_id] = (child, parent)
    return indexed


def _blocks_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return hash_block(a) == hash_block(b)


def get_ast_deltas(
    current_jdf: dict[str, Any] | None,
    previous_jdf: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """
    Compare two JDF trees and return added, modified, or deleted block nodes
    with immediate parent section context for legal coherence.
    """
    current = _iter_blocks(current_jdf)
    previous = _iter_blocks(previous_jdf)
    deltas: list[dict[str, Any]] = []

    for node_id, (node, parent) in current.items():
        prev_entry = previous.get(node_id)
        if prev_entry is None:
            deltas.append(
                {
                    "change": "added",
                    "node_id": node_id,
                    "node": copy.deepcopy(node),
                    "parent": copy.deepcopy(parent),
                    "previous_node": None,
                    "block_hash": hash_block(node),
                    "text": _block_text(node),
                }
            )
            continue
        prev_node, _prev_parent = prev_entry
        if not _blocks_equal(node, prev_node):
            deltas.append(
                {
                    "change": "modified",
                    "node_id": node_id,
                    "node": copy.deepcopy(node),
                    "parent": copy.deepcopy(parent),
                    "previous_node": copy.deepcopy(prev_node),
                    "block_hash": hash_block(node),
                    "text": _block_text(node),
                }
            )

    for node_id, (node, parent) in previous.items():
        if node_id not in current:
            deltas.append(
                {
                    "change": "deleted",
                    "node_id": node_id,
                    "node": copy.deepcopy(node),
                    "parent": copy.deepcopy(parent),
                    "previous_node": copy.deepcopy(node),
                    "block_hash": hash_block(node),
                    "text": _block_text(node),
                }
            )

    return deltas
