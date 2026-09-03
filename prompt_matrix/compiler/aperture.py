"""Compiler helpers for bounded surgical edits."""

from __future__ import annotations

import re
from typing import Any

try:
    from ..cost_governance import TokenAccountant
    from ..models.jdf import JDFDocumentTree, document_to_dict, find_node_index, flatten_nodes
except ImportError:
    from cost_governance import TokenAccountant
    from models.jdf import JDFDocumentTree, document_to_dict, find_node_index, flatten_nodes

SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
SURGICAL_TOKEN_CAP = 2000


def _split_sentences(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    parts = SENTENCE_RE.split(raw)
    return [p.strip() for p in parts if p.strip()]


def _trim_sentences(text: str, *, head: int | None = None, tail: int | None = None) -> str:
    sentences = _split_sentences(text)
    if not sentences:
        return ""
    if head is not None:
        sentences = sentences[:head]
    if tail is not None:
        sentences = sentences[-tail:]
    return " ".join(sentences)


def _node_text(node: dict[str, Any]) -> str:
    ntype = node.get("type")
    if ntype == "paragraph":
        return str(node.get("content") or "")
    if ntype == "callout":
        title = str(node.get("title") or "")
        body = str(node.get("content") or "")
        return f"{title}\n{body}".strip()
    if ntype == "table":
        headers = node.get("headers") or []
        rows = node.get("rows") or []
        lines = [" | ".join(headers)] if headers else []
        for row in rows:
            lines.append(" | ".join(row))
        cap = str(node.get("caption") or "")
        if cap:
            lines.insert(0, cap)
        return "\n".join(lines)
    return ""


def _summarize_node(node: dict[str, Any], max_sentences: int = 2) -> str:
    text = _node_text(node)
    trimmed = _trim_sentences(text, head=max_sentences)
    if trimmed:
        return trimmed
    return text[:240]


def _entity_keys_for_node(node: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for key in node.get("entities_referenced") or []:
        if key:
            keys.add(str(key))
    for key in node.get("bound_entities") or []:
        if key:
            keys.add(str(key))
    return keys


def _ledger_subset(truth_ledger: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    if not keys:
        return {}
    return {k: truth_ledger[k] for k in keys if k in truth_ledger}


def build_aperture_context(tree: JDFDocumentTree | dict[str, Any], target_node_id: str) -> dict[str, Any]:
    """Extract N-1, N, N+1 and bound truth-ledger keys under the surgical token cap."""
    doc = document_to_dict(tree)
    nodes = flatten_nodes(doc)
    hit = find_node_index(doc, target_node_id)
    if hit is None:
        raise KeyError(f"target node not found: {target_node_id}")
    idx, target = hit
    preceding = nodes[idx - 1] if idx > 0 else None
    succeeding = nodes[idx + 1] if idx + 1 < len(nodes) else None

    entity_keys = _entity_keys_for_node(target)
    if preceding:
        entity_keys |= _entity_keys_for_node(preceding)
    if succeeding:
        entity_keys |= _entity_keys_for_node(succeeding)

    truth_subset = _ledger_subset(doc.get("truth_ledger") or {}, entity_keys)

    aperture = {
        "target_node_id": target_node_id,
        "target": {
            "id": target.get("id"),
            "type": target.get("type"),
            "content": _node_text(target),
        },
        "preceding": None,
        "succeeding": None,
        "truth_ledger_subset": truth_subset,
    }

    if preceding:
        aperture["preceding"] = {
            "id": preceding.get("id"),
            "type": preceding.get("type"),
            "summary": _summarize_node(preceding, max_sentences=2),
        }
    if succeeding:
        aperture["succeeding"] = {
            "id": succeeding.get("id"),
            "type": succeeding.get("type"),
            "summary": _summarize_node(succeeding, max_sentences=2),
        }

    accountant = TokenAccountant()
    harness = _render_harness(aperture)
    tokens = accountant.count(harness)
    if tokens > SURGICAL_TOKEN_CAP:
        # Shrink neighbor summaries first, never raw-split target content.
        if aperture["preceding"]:
            aperture["preceding"]["summary"] = _trim_sentences(aperture["preceding"]["summary"], head=1)
        if aperture["succeeding"]:
            aperture["succeeding"]["summary"] = _trim_sentences(aperture["succeeding"]["summary"], head=1)
        harness = _render_harness(aperture)
        tokens = accountant.count(harness)

    aperture["prompt_harness"] = harness
    aperture["token_estimate"] = tokens
    return aperture


def _render_harness(aperture: dict[str, Any]) -> str:
    lines = ["# Aperture context (N-1, N, N+1)", ""]
    if aperture.get("preceding"):
        p = aperture["preceding"]
        lines.append(f"## Preceding ({p['id']})")
        lines.append(p.get("summary") or "")
        lines.append("")
    t = aperture["target"]
    lines.append(f"## Target ({t['id']})")
    lines.append(t.get("content") or "")
    lines.append("")
    if aperture.get("succeeding"):
        s = aperture["succeeding"]
        lines.append(f"## Succeeding ({s['id']})")
        lines.append(s.get("summary") or "")
        lines.append("")
    ledger = aperture.get("truth_ledger_subset") or {}
    if ledger:
        lines.append("## Truth ledger (bound entities)")
        for key, val in ledger.items():
            lines.append(f"- {key}: {val}")
    return "\n".join(lines).strip()


def compile_dspy_aperture_signature() -> dict[str, str]:
    """Optional DSPy signature metadata for external compilation pipelines."""
    try:
        import dspy

        class ApertureSlice(dspy.Signature):
            """Surgical edit bounded to neighboring nodes without narrative drift."""

            preceding_summary = dspy.InputField(desc="Trimmed summary of node N-1")
            target_content = dspy.InputField(desc="Full content of node N")
            succeeding_summary = dspy.InputField(desc="Trimmed summary of node N+1")
            truth_ledger = dspy.InputField(desc="Bound canonical metrics")
            revised_content = dspy.OutputField(desc="Surgically edited node N content")

        return {"signature": ApertureSlice.__name__, "module": "dspy"}
    except ImportError:
        return {
            "signature": "ApertureSlice",
            "module": "local",
            "fields": [
                "preceding_summary",
                "target_content",
                "succeeding_summary",
                "truth_ledger",
                "revised_content",
            ],
        }
