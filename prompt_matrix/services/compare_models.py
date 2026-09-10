"""Parallel two-model compare for Difference Engine (Golden Path Step 3)."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

try:
    from ..cost_router import rewrite_send_id
    from ..lib.ast_diff import text_diff_for_compare
    from ..llm.orchestrator import display_name_for_model, get_compare_pair
    from ..litellm_runner import call_model
except ImportError:
    from cost_router import rewrite_send_id
    from lib.ast_diff import text_diff_for_compare
    from llm.orchestrator import display_name_for_model, get_compare_pair
    from litellm_runner import call_model

_POOL = ThreadPoolExecutor(max_workers=2)


def _intent_messages(intent: str, source_ids: list[str] | None) -> list[dict[str, str]]:
    prompt = str(intent or "").strip()
    if source_ids:
        prompt += "\n\nSource document ids: " + ", ".join(str(s) for s in source_ids if s)
    return [{"role": "user", "content": prompt}]


def run_single_model(
    model: str,
    intent: str,
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Call one LiteLLM model; return normalized compare payload."""
    messages = _intent_messages(intent, source_ids)
    litellm_model = rewrite_send_id(model) or model
    text = call_model(litellm_model, messages, intent="comparison")
    if str(text or "").startswith("ERROR:"):
        raise RuntimeError(str(text))
    body = str(text or "").strip()
    return {
        "model": model,
        "name": display_name_for_model(model),
        "text": body,
        "jdf": {"text": body, "divergences": []},
    }


def run_compare_pair(
    intent: str,
    source_ids: list[str] | None = None,
    *,
    pair_index: int = 0,
) -> dict[str, Any]:
    model_a, model_b = get_compare_pair(pair_index)

    def _safe(model: str) -> dict[str, Any]:
        try:
            return run_single_model(model, intent, source_ids)
        except Exception as exc:
            return {
                "model": model,
                "name": display_name_for_model(model),
                "error": str(exc),
                "text": "",
                "jdf": None,
            }

    result_a = _safe(model_a)
    result_b = _safe(model_b)

    if result_a.get("text") and result_b.get("text"):
        divergences = text_diff_for_compare(result_a["text"], result_b["text"])
        result_a["jdf"] = {"text": result_a["text"], "divergences": divergences}
        result_b["jdf"] = {"text": result_b["text"], "divergences": divergences}

    return {
        "model_a": result_a,
        "model_b": result_b,
        "models": {
            "claude": {
                "name": result_a.get("name") or display_name_for_model(model_a),
                "text": result_a.get("text") or result_a.get("error") or "",
                "error": result_a.get("error"),
            },
            "deepseek": {
                "name": result_b.get("name") or display_name_for_model(model_b),
                "text": result_b.get("text") or result_b.get("error") or "",
                "error": result_b.get("error"),
            },
        },
    }


async def run_compare_pair_async(
    intent: str,
    source_ids: list[str] | None = None,
    *,
    pair_index: int = 0,
) -> dict[str, Any]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _POOL,
        lambda: run_compare_pair(intent, source_ids, pair_index=pair_index),
    )
