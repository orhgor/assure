"""Fast Router — intent classification and vault source ranking (<50ms pre-LLM)."""

from __future__ import annotations

import time
from typing import Any

try:
    from ..db.substrate_repository import fetch_substrate_entries_by_ids, list_included_vault_text
    from ..services.vault_tfidf_cache import (
        cosine_similarity,
        get_cached_index,
        invalidate_workspace_cache,
        query_vector,
    )
except ImportError:
    from db.substrate_repository import fetch_substrate_entries_by_ids, list_included_vault_text
    from services.vault_tfidf_cache import (
        cosine_similarity,
        get_cached_index,
        invalidate_workspace_cache,
        query_vector,
    )

__all__ = ["classify_intent", "detect_sources", "invalidate_workspace_cache"]

_INTENT_RULES: tuple[tuple[str, frozenset[str]], ...] = (
    (
        "extract",
        frozenset({"extract", "pull", "list", "gather", "collect", "enumerate", "quote", "cite"}),
    ),
    (
        "compare",
        frozenset({"compare", "comparison", "versus", "vs", "difference", "contrast", "benchmark"}),
    ),
    (
        "audit",
        frozenset(
            {"audit", "verify", "validate", "check", "stress", "redhat", "red-hat", "contradiction"}
        ),
    ),
    (
        "draft",
        frozenset(
            {
                "draft",
                "write",
                "summarize",
                "summary",
                "compose",
                "narrative",
                "report",
                "update",
                "memo",
            }
        ),
    ),
)


def _tokenize(text: str) -> list[str]:
    import re

    return re.findall(r"[a-z0-9]+", (text or "").lower())


def classify_intent(intent: str) -> str:
    """Classify user intent as extract, compare, audit, draft, or unknown."""
    tokens = set(_tokenize(intent))
    if not tokens:
        return "unknown"
    scores: dict[str, int] = {label: len(tokens & keywords) for label, keywords in _INTENT_RULES}
    best = max(scores.items(), key=lambda item: item[1])
    if best[1] == 0:
        return "draft"
    return best[0]


def detect_sources(
    intent: str,
    workspace_id: str,
    *,
    explicit_source_ids: list[str] | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Rank vault sources by TF-IDF relevance to intent."""
    ws = workspace_id or "default"
    if explicit_source_ids:
        rows = fetch_substrate_entries_by_ids(ws, explicit_source_ids)
    else:
        rows = list_included_vault_text(ws)
    if not rows:
        return []

    index = get_cached_index(ws, rows)
    q_vec = query_vector(_tokenize(intent), index.idf)
    scored: list[tuple[float, dict[str, Any]]] = []
    for source in index.sources:
        sid = str(source["id"])
        score = cosine_similarity(q_vec, index.doc_vectors.get(sid, {}))
        item = dict(source)
        item["score"] = round(score, 4)
        scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[: max(1, limit)]]
