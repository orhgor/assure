"""Parallel two-model compare for Difference Engine (Golden Path Step 3)."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

try:
    from ..config.system_prompt import COMPARE_FREE_INSTRUCTION
    from ..cost_router import rewrite_send_id
    from ..keys import litellm_kwargs_for, provider_slug_for_litellm
    from ..lib.ast_diff import text_diff_for_compare
    from ..llm.orchestrator import (
        display_name_for_model,
        family_of,
        free_pairs_different_families,
        get_compare_pair,
        timeout_for,
        use_free_models,
    )
    from ..litellm_runner import call_model
except ImportError:
    from config.system_prompt import COMPARE_FREE_INSTRUCTION
    from cost_router import rewrite_send_id
    from keys import litellm_kwargs_for, provider_slug_for_litellm
    from lib.ast_diff import text_diff_for_compare
    from llm.orchestrator import (
        display_name_for_model,
        family_of,
        free_pairs_different_families,
        get_compare_pair,
        timeout_for,
        use_free_models,
    )
    from litellm_runner import call_model

log = logging.getLogger(__name__)
_POOL = ThreadPoolExecutor(max_workers=2)


def _intent_messages(intent: str, source_ids: list[str] | None) -> list[dict[str, str]]:
    prompt = str(intent or "").strip()
    if source_ids:
        prompt += "\n\nSource document ids: " + ", ".join(str(s) for s in source_ids if s)
    return [
        {"role": "system", "content": COMPARE_FREE_INSTRUCTION},
        {"role": "user", "content": prompt},
    ]


def run_single_model(
    model: str,
    intent: str,
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Call one LiteLLM model; return normalized compare payload."""
    messages = _intent_messages(intent, source_ids)
    litellm_model = rewrite_send_id(model) or model
    kwargs = litellm_kwargs_for(provider_slug_for_litellm(litellm_model))
    text = call_model(
        litellm_model,
        messages,
        intent="comparison",
        skip_language_guard=True,
        timeout=timeout_for(litellm_model),
        **kwargs,
    )
    if str(text or "").startswith("ERROR:"):
        raise RuntimeError(str(text))
    body = str(text or "").strip()
    if not body:
        raise RuntimeError(f"Empty response from {model}")
    return {
        "model": model,
        "name": display_name_for_model(model),
        "text": body,
        "jdf": {"text": body, "divergences": []},
    }


def _safe_model(model: str, intent: str, source_ids: list[str] | None) -> dict[str, Any]:
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


def _model_slot(result: dict[str, Any], model_id: str) -> dict[str, Any]:
    return {
        "name": result.get("name") or display_name_for_model(model_id),
        "text": result.get("text") or "",
        "error": result.get("error"),
    }


def _pair_has_usable_result(result_a: dict[str, Any], result_b: dict[str, Any]) -> bool:
    return bool((result_a.get("text") or "").strip()) or bool((result_b.get("text") or "").strip())


def _finalize_pair(
    model_a: str,
    model_b: str,
    result_a: dict[str, Any],
    result_b: dict[str, Any],
) -> dict[str, Any]:
    if result_a.get("text") and result_b.get("text"):
        divergences = text_diff_for_compare(result_a["text"], result_b["text"])
        result_a["jdf"] = {"text": result_a["text"], "divergences": divergences}
        result_b["jdf"] = {"text": result_b["text"], "divergences": divergences}
    elif result_a.get("text"):
        result_a["jdf"] = {"text": result_a["text"], "divergences": []}
    elif result_b.get("text"):
        result_b["jdf"] = {"text": result_b["text"], "divergences": []}
    return {
        "model_a": result_a,
        "model_b": result_b,
        "pair": (model_a, model_b),
        "families": (family_of(model_a), family_of(model_b)),
        "models": {
            "claude": _model_slot(result_a, model_a),
            "deepseek": _model_slot(result_b, model_b),
        },
    }


def _candidate_pairs(pair_index: int) -> list[tuple[str, str]]:
    if use_free_models():
        pairs = free_pairs_different_families()
        if not pairs:
            raise RuntimeError(
                "No free model pair has two different families. "
                "Diff engine cannot produce divergence."
            )
        # Start at pair_index, then wrap through the rest for fallback.
        return pairs[pair_index:] + pairs[:pair_index]
    model_a, model_b = get_compare_pair(pair_index)
    return [(model_a, model_b)]


def run_compare_pair(
    intent: str,
    source_ids: list[str] | None = None,
    *,
    pair_index: int = 0,
) -> dict[str, Any]:
    last_error = "All free pairs failed. Check API keys and rate limits."
    for idx, (model_a, model_b) in enumerate(_candidate_pairs(pair_index)):
        if family_of(model_a) == family_of(model_b):
            log.warning("[free-stack] skipping same-family pair %s vs %s", model_a, model_b)
            continue
        fut_a = _POOL.submit(_safe_model, model_a, intent, source_ids)
        fut_b = _POOL.submit(_safe_model, model_b, intent, source_ids)
        result_a = fut_a.result()
        result_b = fut_b.result()
        if _pair_has_usable_result(result_a, result_b):
            if idx > 0:
                log.warning("[free-stack] fell back to pair index offset %s", idx)
            return _finalize_pair(model_a, model_b, result_a, result_b)
        last_error = (
            f"pair failed ({model_a} / {model_b}): "
            f"{result_a.get('error') or 'ok'} | {result_b.get('error') or 'ok'}"
        )
        log.warning("[free-stack] %s — trying next", last_error)
        if not use_free_models():
            return _finalize_pair(model_a, model_b, result_a, result_b)
    raise RuntimeError(last_error)


async def run_compare_pair_async(
    intent: str,
    source_ids: list[str] | None = None,
    *,
    pair_index: int = 0,
) -> dict[str, Any]:
    loop = asyncio.get_event_loop()
    last_error = "All free pairs failed. Check API keys and rate limits."
    for idx, (model_a, model_b) in enumerate(_candidate_pairs(pair_index)):
        if family_of(model_a) == family_of(model_b):
            log.warning("[free-stack] skipping same-family pair %s vs %s", model_a, model_b)
            continue
        result_a, result_b = await asyncio.gather(
            loop.run_in_executor(_POOL, _safe_model, model_a, intent, source_ids),
            loop.run_in_executor(_POOL, _safe_model, model_b, intent, source_ids),
        )
        if _pair_has_usable_result(result_a, result_b):
            if idx > 0:
                log.warning("[free-stack] fell back to pair index offset %s", idx)
            return _finalize_pair(model_a, model_b, result_a, result_b)
        last_error = (
            f"pair failed ({model_a} / {model_b}): "
            f"{result_a.get('error') or 'ok'} | {result_b.get('error') or 'ok'}"
        )
        log.warning("[free-stack] %s — trying next", last_error)
        if not use_free_models():
            return _finalize_pair(model_a, model_b, result_a, result_b)
    raise RuntimeError(last_error)
