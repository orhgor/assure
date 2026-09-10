"""Main document grammar/flow polish with strict lock-pill preservation."""

from __future__ import annotations

import copy
import re
from typing import Any

try:
    from ..lib.sanitize import sanitize_jdf_node
    from ..models.jdf import parse_document
except ImportError:
    from lib.sanitize import sanitize_jdf_node
    from models.jdf import parse_document

LOCK_TOKEN_RE = re.compile(r"\[🔒 #\d+\]")


def _paragraph_nodes(document: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for section in document.get("body") or []:
        if not isinstance(section, dict):
            continue
        for child in section.get("children") or []:
            if isinstance(child, dict) and child.get("type") == "paragraph":
                nodes.append(child)
    return nodes


def collect_lock_pills(document: dict[str, Any]) -> list[dict[str, Any]]:
    pills: list[dict[str, Any]] = []
    for node in _paragraph_nodes(document):
        meta = node.get("meta") or {}
        for pill in meta.get("lock_pills") or []:
            if not isinstance(pill, dict):
                continue
            lock_hash = str(pill.get("lock_hash") or "").strip()
            if lock_hash:
                pills.append(copy.deepcopy(pill))
    return pills


def _pill_signature(pill: dict[str, Any]) -> tuple[str, str, int]:
    return (
        str(pill.get("lock_hash") or "").strip(),
        str(pill.get("source_id") or "").strip(),
        int(pill.get("lock_index") or 0),
    )


def strict_preservation_check(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Ensure lock_hash pills in meta are unchanged across polish."""
    before_pills = collect_lock_pills(before)
    after_pills = collect_lock_pills(after)
    if len(before_pills) != len(after_pills):
        return False
    before_sigs = sorted(_pill_signature(p) for p in before_pills)
    after_sigs = sorted(_pill_signature(p) for p in after_pills)
    return before_sigs == after_sigs


def document_plain_text(document: dict[str, Any]) -> str:
    parts: list[str] = []
    for node in _paragraph_nodes(document):
        content = str(node.get("content") or "").strip()
        if content:
            parts.append(content)
    return "\n\n".join(parts)


def _polish_prose(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return cleaned
    cleaned = cleaned[0].upper() + cleaned[1:] if len(cleaned) > 1 else cleaned.upper()
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"([,.;:!?])([^\s])", r"\1 \2", cleaned)
    if cleaned[-1] not in ".!?":
        cleaned += "."
    return cleaned


def _polish_with_llm(text: str) -> str | None:
    if not text.strip():
        return text
    try:
        from ..litellm_runner import call_model
    except ImportError:
        from litellm_runner import call_model

    prompt = (
        "Rewrite the following for professional grammar and flow. "
        "Do not remove or alter bracketed lock markers like [🔒 #01]. "
        "Do not change numbers, names, or verified facts.\n\n"
        f"{text}"
    )
    try:
        result = call_model(
            "gemini/gemini-2.0-flash",
            [{"role": "user", "content": prompt}],
            max_tokens=2000,
            intent="analysis",
        )
        polished = str(result or "").strip()
        return polished or None
    except Exception:
        return None


def polish_text_preserving_lock_tokens(text: str, *, use_llm: bool = True) -> str:
    segments: list[str] = []
    cursor = 0
    for match in LOCK_TOKEN_RE.finditer(text):
        chunk = text[cursor : match.start()]
        if chunk:
            polished_chunk = _polish_with_llm(chunk) if use_llm else None
            segments.append(polished_chunk if polished_chunk else _polish_prose(chunk))
        segments.append(match.group(0))
        cursor = match.end()
    tail = text[cursor:]
    if tail:
        polished_tail = _polish_with_llm(tail) if use_llm else None
        segments.append(polished_tail if polished_tail else _polish_prose(tail))
    return "".join(segments).strip()


def polish_document_tree(document: dict[str, Any], *, use_llm: bool = True) -> dict[str, Any]:
    polished = copy.deepcopy(document)
    for node in _paragraph_nodes(polished):
        meta = dict(node.get("meta") or {})
        lock_pills = meta.get("lock_pills") or []
        original_content = str(node.get("content") or "")
        node["content"] = polish_text_preserving_lock_tokens(original_content, use_llm=use_llm)
        meta["lock_pills"] = copy.deepcopy(lock_pills)
        node["meta"] = meta
    return polished


def run_polish_document(document: dict[str, Any], *, use_llm: bool = True) -> dict[str, Any]:
    tree = sanitize_jdf_node(copy.deepcopy(document))
    parse_document(tree)
    before_pills = collect_lock_pills(tree)
    polished = polish_document_tree(tree, use_llm=use_llm)
    polished = sanitize_jdf_node(polished)
    parse_document(polished)
    preserved = strict_preservation_check(tree, polished)
    return {
        "status": "success" if preserved else "error",
        "document": polished,
        "plain_before": document_plain_text(tree),
        "plain_after": document_plain_text(polished),
        "strict_preservation": preserved,
        "lock_hashes": [str(p.get("lock_hash") or "") for p in before_pills],
        "lock_count": len(before_pills),
    }
