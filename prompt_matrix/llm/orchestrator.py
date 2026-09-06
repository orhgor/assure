"""LiteLLM Router for node-type-aware model selection."""

from __future__ import annotations

import os
from typing import Any

try:
    from litellm import Router
except ImportError:  # pragma: no cover
    Router = None  # type: ignore[misc, assignment]

_model_list = [
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
            "api_key": os.getenv("GEMINI_API_KEY"),
        },
    },
    {
        "model_name": "vision-analysis",
        "litellm_params": {
            "model": "anthropic/claude-3-5-sonnet-20240620",
            "api_key": os.getenv("ANTHROPIC_API_KEY"),
        },
    },
]

_assure_router: Any | None = None


def get_router() -> Any:
    global _assure_router
    if _assure_router is not None:
        return _assure_router
    if Router is None:
        raise RuntimeError("litellm Router unavailable")
    _assure_router = Router(
        model_list=_model_list,
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
