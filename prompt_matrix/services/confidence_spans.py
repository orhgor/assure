"""Z3 / ledger confidence spans for the workbench overlay.

Offsets are character indexes into that node's visible text (paragraph
content, callout content, or section title). Scores are 0.0–1.0.
"""

from __future__ import annotations

import re
from typing import Any

_SENTENCE = re.compile(r"[^.!?]+[.!?]|[^.!?]+$", re.S)
_VIOLATION_KEY = re.compile(r"Metric '([^']+)'")


def _node_text(node: dict[str, Any]) -> str:
    ntype = str(node.get("type") or "")
    if ntype == "section":
        return str(node.get("title") or "")
    if ntype == "callout":
        title = str(node.get("title") or "").strip()
        content = str(node.get("content") or "")
        return f"{title}\n{content}".strip() if title else content
    if ntype == "table":
        return str(node.get("caption") or "")
    return str(node.get("content") or "")


def _violation_keys(z3_results: dict[str, Any] | None) -> set[str]:
    keys: set[str] = set()
    for item in (z3_results or {}).get("violations") or []:
        match = _VIOLATION_KEY.search(str(item) or "")
        if match:
            keys.add(match.group(1))
    return keys


def _verified_lock_keys(z3_results: dict[str, Any] | None, ledger: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for item in (z3_results or {}).get("lock_results") or []:
        if item.get("ok") and item.get("key"):
            keys.add(str(item["key"]))
    for key in ledger:
        if key:
            keys.add(str(key))
    return keys


def _iter_units(text: str) -> list[tuple[int, int, str]]:
    """Sentence-sized slices; fall back to the whole string."""
    if not (text or "").strip():
        return []
    units: list[tuple[int, int, str]] = []
    for match in _SENTENCE.finditer(text):
        raw = match.group(0)
        start, end = match.start(), match.end()
        while end > start and text[end - 1] in " \t\r\n":
            end -= 1
        chunk = text[start:end]
        if chunk.strip():
            units.append((start, end, chunk))
    if units:
        return units
    end = len(text)
    while end > 0 and text[end - 1] in " \t\r\n":
        end -= 1
    return [(0, end, text[:end])] if end else []


def _score_unit(
    chunk: str,
    node: dict[str, Any],
    *,
    violation_keys: set[str],
    lock_keys: set[str],
) -> tuple[float, str]:
    lowered = chunk.lower()
    for key in violation_keys:
        if key.lower() in lowered:
            return 0.25, "z3"
    annotations = (node.get("annotations") or {}).get("z3") or []
    for ann in annotations:
        if str(ann.get("status") or "") != "violation":
            continue
        canonical = str(ann.get("canonical_key") or "")
        if canonical and canonical.lower() in lowered:
            return 0.25, "z3"
    provenance = node.get("provenance") or []
    if provenance:
        first = provenance[0] if isinstance(provenance[0], dict) else {}
        source = str(first.get("source_name") or first.get("source_id") or "substrate")
        return 0.9, source
    for key in lock_keys:
        if key.lower() in lowered:
            return 0.95, "z3"
    return 0.55, "ledger"


def _walk_nodes(document: dict[str, Any]) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    for section in document.get("body") or []:
        if isinstance(section, dict):
            ordered.append(section)
            for child in section.get("children") or []:
                if isinstance(child, dict):
                    ordered.append(child)
    return ordered


def build_confidence_spans(
    document: dict[str, Any] | None,
    z3_results: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return ``{ startChar, endChar, score, source, nodeId }`` for each claim unit."""
    if not document:
        return []
    ledger = document.get("truth_ledger") or {}
    violation_keys = _violation_keys(z3_results)
    lock_keys = _verified_lock_keys(z3_results, ledger if isinstance(ledger, dict) else {})
    spans: list[dict[str, Any]] = []
    for node in _walk_nodes(document):
        text = _node_text(node)
        node_id = str(node.get("id") or "")
        for start, end, chunk in _iter_units(text):
            score, source = _score_unit(
                chunk,
                node,
                violation_keys=violation_keys,
                lock_keys=lock_keys,
            )
            spans.append(
                {
                    "startChar": start,
                    "endChar": end,
                    "score": score,
                    "source": source,
                    "nodeId": node_id,
                }
            )
    return spans


def attach_confidence_spans_to_document(
    document: dict[str, Any],
    spans: list[dict[str, Any]],
) -> dict[str, Any]:
    doc = dict(document)
    meta = dict(doc.get("meta") or {})
    meta["confidenceSpans"] = spans
    doc["meta"] = meta
    return doc


def _redhat_texts(ann: Any) -> list[str]:
    texts: list[str] = []
    for item in ann or []:
        if isinstance(item, dict):
            blob = str(item.get("text") or item.get("content") or item.get("title") or "").strip()
        else:
            blob = str(item or "").strip()
        if blob:
            texts.append(blob)
    return texts


def _critique_for_node(
    node: dict[str, Any],
    critiques: list[dict[str, Any]],
    *,
    is_first: bool,
) -> str:
    texts = _redhat_texts((node.get("annotations") or {}).get("redhat"))
    if texts:
        return " ".join(texts)
    nid = str(node.get("id") or "")
    for crit in critiques:
        tid = str(crit.get("target_node_id") or crit.get("nodeId") or crit.get("node_id") or "")
        if tid and tid == nid:
            blob = str(crit.get("content") or crit.get("text") or crit.get("title") or "").strip()
            if blob:
                return blob
    if is_first and critiques:
        first = critiques[0]
        return str(first.get("content") or first.get("text") or first.get("title") or "").strip()
    return ""


def build_macro_appendix(
    document: dict[str, Any] | None,
    z3_results: dict[str, Any] | None = None,
    redhat_critiques: list[dict[str, Any]] | None = None,
    confidence_spans: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """One row per claim: visible text, Z3 score, and Red-Hat critique."""
    if not document:
        return []
    spans = (
        confidence_spans
        if confidence_spans is not None
        else build_confidence_spans(document, z3_results=z3_results)
    )
    critiques = redhat_critiques or []
    span_by_key: dict[tuple[str, int, int], dict[str, Any]] = {}
    for span in spans:
        nid = str(span.get("nodeId") or span.get("node_id") or "")
        start = int(
            span.get("startChar") if span.get("startChar") is not None else span.get("start") or 0
        )
        end = int(span.get("endChar") if span.get("endChar") is not None else span.get("end") or 0)
        span_by_key[(nid, start, end)] = span
    rows: list[dict[str, Any]] = []
    first = True
    for node in _walk_nodes(document):
        text = _node_text(node)
        nid = str(node.get("id") or "")
        critique = _critique_for_node(node, critiques, is_first=first)
        first = False
        units = _iter_units(text)
        if not units:
            continue
        for start, end, chunk in units:
            span = span_by_key.get((nid, start, end))
            score = span.get("score") if span else None
            rows.append(
                {
                    "claim": chunk.strip(),
                    "nodeId": nid,
                    "z3Score": score,
                    "z3_score": score,
                    "redhatCritique": critique,
                    "redhat_critique": critique,
                }
            )
    return rows
