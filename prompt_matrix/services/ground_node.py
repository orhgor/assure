"""Hybrid surgical grounding: Brave search and/or main LLM rewrite."""

from __future__ import annotations

import copy
import json
import os
import re
from typing import Any

try:
    from ..compiler.aperture import build_aperture_context
    from ..db.jdf_repository import fetch_latest_jdf_or_empty
    from ..lib.sanitize import sanitize_jdf_node
    from ..models.jdf import get_node_by_id
except ImportError:
    from compiler.aperture import build_aperture_context
    from db.jdf_repository import fetch_latest_jdf_or_empty
    from lib.sanitize import sanitize_jdf_node
    from models.jdf import get_node_by_id

SEARCH_MODEL = os.environ.get("ASSURE_GROUND_SEARCH_MODEL") or "deepseek/deepseek-chat"
LLM_MODEL = os.environ.get("ASSURE_GROUND_LLM_MODEL") or "anthropic/claude-sonnet-4-5"
_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")

SEARCH_PROMPT = (
    "Rewrite the following paragraph using the provided search snippets. "
    "Correct factual errors. Output only the revised JSON for this node."
)
LLM_PROMPT = (
    "Rewrite this paragraph to fix factual errors or improve clarity using your "
    "internal knowledge. Do not invent facts. Output only the revised JSON for this node."
)


def search_brave_web(query: str, *, count: int = 3) -> list[dict[str, str]]:
    token = (os.environ.get("BRAVE_API_KEY") or "").strip()
    q = (query or "").strip()[:200]
    if not token or not q:
        return []
    try:
        import httpx
    except ImportError:
        return []
    response = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": q, "count": count},
        headers={"Accept": "application/json", "X-Subscription-Token": token},
        timeout=20.0,
    )
    response.raise_for_status()
    web = (response.json() or {}).get("web") or {}
    rows = []
    for item in (web.get("results") or [])[:count]:
        rows.append(
            {
                "source_url": str(item.get("url") or ""),
                "snippet": str(item.get("description") or item.get("title") or ""),
                "title": str(item.get("title") or ""),
            }
        )
    return rows


def _complete(model: str, messages: list[dict[str, str]]) -> str:
    try:
        from ..litellm_runner import call_model
    except ImportError:
        from litellm_runner import call_model
    return call_model(model, messages, max_tokens=1200, intent="analysis")


def _parse_node_json(raw: str, fallback: dict[str, Any]) -> dict[str, Any]:
    text = (raw or "").strip()
    match = _JSON_BLOCK.search(text)
    blob = match.group(0) if match else text
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError:
        out = copy.deepcopy(fallback)
        out["content"] = text or str(fallback.get("content") or "")
        return out
    if not isinstance(parsed, dict):
        out = copy.deepcopy(fallback)
        out["content"] = str(parsed)
        return out
    merged = copy.deepcopy(fallback)
    merged.update(parsed)
    merged["id"] = fallback.get("id")
    merged["type"] = fallback.get("type") or merged.get("type") or "paragraph"
    return merged


def _node_text(node: dict[str, Any]) -> str:
    return str(node.get("content") or node.get("title") or "")


def _stamp_attribution(node: dict[str, Any], attribution: list[dict[str, str]]) -> dict[str, Any]:
    out = copy.deepcopy(node)
    meta = dict(out.get("meta") or {})
    meta["search_attribution"] = attribution
    out["meta"] = meta
    return sanitize_jdf_node(out)


def _rewrite_with_search(node: dict[str, Any], snippets: list[dict[str, str]]) -> dict[str, Any]:
    payload = {
        "node": node,
        "snippets": snippets,
        "instruction": SEARCH_PROMPT,
    }
    raw = _complete(
        SEARCH_MODEL,
        [
            {"role": "system", "content": SEARCH_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )
    return _parse_node_json(raw, node)


def _rewrite_with_llm(tree: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    aperture = build_aperture_context(tree, str(node.get("id") or ""))
    payload = {
        "node": node,
        "aperture": aperture,
        "instruction": LLM_PROMPT,
    }
    raw = _complete(
        LLM_MODEL,
        [
            {"role": "system", "content": LLM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )
    return _parse_node_json(raw, node)


def ground_node(
    project_id: str,
    node_id: str,
    *,
    mode: str = "auto",
    document: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mode_key = (mode or "auto").strip().lower()
    if mode_key not in {"search", "llm", "auto"}:
        raise ValueError("mode must be search, llm, or auto")
    tree = document or fetch_latest_jdf_or_empty(project_id)
    original = get_node_by_id(tree, node_id)
    if original is None:
        raise KeyError(f"node not found: {node_id}")
    original = copy.deepcopy(original)
    query = _node_text(original)[:200]
    snippets: list[dict[str, str]] = []
    method = "llm"
    if mode_key in {"search", "auto"}:
        snippets = search_brave_web(query)
        total = sum(len(item.get("snippet") or "") for item in snippets)
        if mode_key == "search" or total >= 100:
            method = "search"
        else:
            method = "llm"
    if method == "search":
        suggested = _rewrite_with_search(original, snippets)
        suggested = _stamp_attribution(suggested, snippets)
    else:
        suggested = _rewrite_with_llm(tree, original)
        suggested = sanitize_jdf_node(suggested)
        if snippets:
            suggested = _stamp_attribution(suggested, snippets)
    return {
        "ok": True,
        "original": original,
        "suggested": suggested,
        "node": suggested,
        "method_used": method,
        "search_attribution": snippets,
    }
