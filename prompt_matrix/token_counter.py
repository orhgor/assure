"""Token counts via tiktoken. Replaces the chars/4 heuristic."""

from __future__ import annotations

from typing import Optional

import tiktoken


def count_tokens(text: str, model: Optional[str] = None) -> int:
    """
    Count tokens using tiktoken. Falls back to cl100k_base if model unknown.
    Replaces the chars/4 heuristic entirely.
    """
    try:
        if model:
            encoding = tiktoken.encoding_for_model(_model_name(model))
        else:
            encoding = tiktoken.get_encoding("cl100k_base")
    except (KeyError, ValueError):
        encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(text or ""))


def _model_name(model: str) -> str:
    raw = (model or "").strip()
    if "/" in raw:
        return raw.split("/", 1)[1]
    return raw
