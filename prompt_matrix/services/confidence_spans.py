"""Z3 / ledger consistency spans for the workbench overlay.

Offsets are character indexes into that node's visible text (paragraph
content, callout content, or section title).

Each span carries ``numeric_consistency`` — ``matches_lock`` / ``no_lock`` /
``contradicts_lock`` — and ``score: None``. Until 2026-09-27 ``score`` was
0.92 / 0.5 / 0.25 (fixed values from ``ledger/truth_engine``) and the shell
painted them green / yellow / red as "confidence"; a fixed number is not a
measurement, and a sentence with no figure to check is not "50 % confident".
The ``score`` key stays, null, so a reader that looks for it finds an
explicit absence (shell contract: ``docs/evidence-honesty.md``).
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
    ledger: dict[str, Any] | None = None,
    context: str = "",
) -> tuple[str, str, str]:
    """``(numeric_consistency, source, reason)`` for one claim unit."""
    try:
        from ..ledger.truth_engine import NUMERIC_CONTRADICTS_LOCK
        from ..ledger.z3_ledger import check_claim
    except ImportError:
        from ledger.truth_engine import NUMERIC_CONTRADICTS_LOCK
        from ledger.z3_ledger import check_claim
    checked = check_claim(
        chunk,
        context=context,
        ledger=ledger,
        source_label=_source_label(node),
        source_id=_source_id(node),
    )
    consistency = str(checked.get("numeric_consistency") or "no_lock")
    lowered = chunk.lower()
    for key in violation_keys:
        if key.lower() in lowered:
            return NUMERIC_CONTRADICTS_LOCK, "z3", checked["reason"]
    annotations = (node.get("annotations") or {}).get("z3") or []
    for ann in annotations:
        if str(ann.get("status") or "") != "violation":
            continue
        canonical = str(ann.get("canonical_key") or "")
        if canonical and canonical.lower() in lowered:
            return NUMERIC_CONTRADICTS_LOCK, "z3", checked["reason"]
    provenance = node.get("provenance") or []
    if provenance:
        first = provenance[0] if isinstance(provenance[0], dict) else {}
        source = str(first.get("source_name") or first.get("source_id") or "substrate")
        return consistency, source, checked["reason"]
    for key in lock_keys:
        if key.lower() in lowered:
            return consistency, "z3", checked["reason"]
    return consistency, "ledger", checked["reason"]


def _source_label(node: dict[str, Any]) -> str:
    for prov in node.get("provenance") or []:
        if isinstance(prov, dict) and (prov.get("source_name") or prov.get("page_number")):
            name = str(prov.get("source_name") or "source")
            page = str(prov.get("page_number") or "")
            section = str(prov.get("extracted_quote") or "")[:80]
            bits = [name]
            if page:
                bits.append(f"p.{page}")
            if section:
                bits.append(section)
            return ", ".join(bits)
    return "source text"


def _node_source_text(node: dict[str, Any]) -> str:
    """The anchored source sentences (window, else quote) of every provenance
    row, joined — the only text a claim may be searched in. Until 2026-09-27
    the node's own content was the context, so every unit "matched 100 % of
    source phrase" — itself."""
    parts: list[str] = []
    for prov in node.get("provenance") or []:
        if not isinstance(prov, dict):
            continue
        text = str(prov.get("anchor_window") or "").strip() or str(
            prov.get("extracted_quote") or ""
        ).strip()
        if text and text not in parts:
            parts.append(text)
    return "\n".join(parts)


def _source_id(node: dict[str, Any]) -> str:
    for prov in node.get("provenance") or []:
        if isinstance(prov, dict) and prov.get("source_id"):
            return str(prov.get("source_id") or "")
    return ""


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
    """Return ``{ startChar, endChar, numeric_consistency, score: None, source,
    nodeId, reason }`` for each claim unit."""
    if not document:
        return []
    ledger = document.get("truth_ledger") or {}
    violation_keys = _violation_keys(z3_results)
    lock_keys = _verified_lock_keys(z3_results, ledger if isinstance(ledger, dict) else {})
    spans: list[dict[str, Any]] = []
    node_spans: dict[str, list[dict[str, Any]]] = {}
    for node in _walk_nodes(document):
        text = _node_text(node)
        node_id = str(node.get("id") or "")
        source_text = _node_source_text(node)
        for start, end, chunk in _iter_units(text):
            consistency, source, reason = _score_unit(
                chunk,
                node,
                violation_keys=violation_keys,
                lock_keys=lock_keys,
                ledger=ledger if isinstance(ledger, dict) else {},
                context=source_text,
            )
            span = {
                "startChar": start,
                "endChar": end,
                "score": None,
                "numeric_consistency": consistency,
                "source": source,
                "nodeId": node_id,
                "reason": reason,
            }
            spans.append(span)
            node_spans.setdefault(node_id, []).append(span)
    for node in _walk_nodes(document):
        nid = str(node.get("id") or "")
        meta = dict(node.get("meta") or {})
        if nid in node_spans:
            meta["confidenceSpans"] = node_spans[nid]
            node["meta"] = meta
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
    """One row per claim: visible text, Z3 numeric consistency, and Red-Hat critique.

    ``z3Score`` / ``z3_score`` are always ``None`` (2026-09-27); the verdict
    word is ``numeric_consistency``."""
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
            consistency = span.get("numeric_consistency") if span else None
            rows.append(
                {
                    "claim": chunk.strip(),
                    "nodeId": nid,
                    "z3Score": None,
                    "z3_score": None,
                    "numeric_consistency": consistency,
                    "redhatCritique": critique,
                    "redhat_critique": critique,
                }
            )
    return rows
