"""LiteLLM Router for node-type-aware model selection."""

from __future__ import annotations

import os
from typing import Any

try:
    from litellm import Router
except ImportError:  # pragma: no cover
    Router = None  # type: ignore[misc, assignment]

# Staging free stack — Gemini + DeepSeek (no paid Claude on left pane).
# Keys match Difference Engine UI slots (claude / deepseek).
FREE_MODEL_PAIRS: dict[str, dict[str, str]] = {
    "claude": {
        "name": "Gemini 2.0 Flash",
        "litellm_model": "gemini/gemini-2.0-flash",
        "provider": "gemini",
    },
    "deepseek": {
        "name": "DeepSeek V3",
        "litellm_model": "deepseek/deepseek-chat",
        "provider": "deepseek",
    },
}

_DEFAULT_MODEL_PAIRS: dict[str, dict[str, str]] = {
    "claude": {
        "name": "Claude 3.5 Sonnet",
        "litellm_model": "anthropic/claude-3-5-sonnet-20240620",
        "provider": "claude",
    },
    "deepseek": {
        "name": "DeepSeek V3",
        "litellm_model": "deepseek/deepseek-chat",
        "provider": "deepseek",
    },
}

_paid_model_list = [
    {
        "model_name": "text-reasoning",
        "litellm_params": {
            "model": "deepseek/deepseek-chat",
            "api_key": os.getenv("DEEPSEEK_API_KEY"),
            "max_tokens": 4096,
        },
    },
    {
        "model_name": "table-parsing",
        "litellm_params": {
            "model": "gemini/gemini-1.5-pro",
            "api_key": os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
        },
    },
    {
        "model_name": "vision-analysis",
        "litellm_params": {
            "model": "anthropic/claude-3-5-sonnet-20240620",
            "api_key": os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY"),
        },
    },
]

_assure_router: Any | None = None


def use_free_models() -> bool:
    """True when ASSURE_USE_FREE_MODELS is set (staging compose default)."""
    return os.getenv("ASSURE_USE_FREE_MODELS", "").strip().lower() in ("1", "true", "yes")


def orchestrator_model_pairs() -> dict[str, dict[str, str]]:
    """Side-by-side orchestrator slots → display name + LiteLLM id."""
    if use_free_models():
        return dict(FREE_MODEL_PAIRS)
    return dict(_DEFAULT_MODEL_PAIRS)


def _gemini_api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def _build_model_list() -> list[dict[str, Any]]:
    if not use_free_models():
        return _paid_model_list
    gemini = FREE_MODEL_PAIRS["claude"]["litellm_model"]
    deepseek = FREE_MODEL_PAIRS["deepseek"]["litellm_model"]
    return [
        {
            "model_name": "text-reasoning",
            "litellm_params": {
                "model": deepseek,
                "api_key": os.getenv("DEEPSEEK_API_KEY"),
                "max_tokens": 4096,
            },
        },
        {
            "model_name": "table-parsing",
            "litellm_params": {
                "model": gemini,
                "api_key": _gemini_api_key(),
            },
        },
        {
            "model_name": "vision-analysis",
            "litellm_params": {
                "model": gemini,
                "api_key": _gemini_api_key(),
            },
        },
    ]


def get_router() -> Any:
    global _assure_router
    if _assure_router is not None:
        return _assure_router
    if Router is None:
        raise RuntimeError("litellm Router unavailable")
    _assure_router = Router(
        model_list=_build_model_list(),
        routing_strategy="latency-based-routing",
        num_retries=3,
        allowed_fails=2,
        cooldown_time=10,
    )
    return _assure_router


def model_for_node_type(node_type: str) -> str:
    if node_type in ("table", "financial_grid"):
        return "table-parsing"
    if node_type in ("image", "chart"):
        return "vision-analysis"
    return "text-reasoning"


async def orchestrate_node_compilation(
    node_type: str, prompt_messages: list[dict[str, str]]
) -> str:
    target_model = model_for_node_type(node_type)
    response = await get_router().acompletion(
        model=target_model,
        messages=prompt_messages,
        timeout=60,
    )
    return response.choices[0].message.content


def orchestrate_node_compilation_sync(node_type: str, prompt_messages: list[dict[str, str]]) -> str:
    """Sync wrapper for Flask/Celery call sites."""
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    asyncio.run,
                    orchestrate_node_compilation(node_type, prompt_messages),
                ).result()
        return loop.run_until_complete(orchestrate_node_compilation(node_type, prompt_messages))
    except RuntimeError:
        return asyncio.run(orchestrate_node_compilation(node_type, prompt_messages))
