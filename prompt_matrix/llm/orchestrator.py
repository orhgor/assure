"""LiteLLM Router + Difference Engine model-pair selection (staging free stack)."""

from __future__ import annotations

import os
from typing import Any

try:
    from litellm import Router
except ImportError:  # pragma: no cover
    Router = None  # type: ignore[misc, assignment]

# Two genuinely different providers — required for the diff engine.
FREE_MODEL_PAIRS: list[tuple[str, str]] = [
    ("gemini/gemini-3.6-flash", "deepseek/deepseek-chat"),
    ("groq/llama-3.3-70b-versatile", "gemini/gemini-3.6-flash"),
]

PRODUCTION_MODEL_PAIRS: list[tuple[str, str]] = [
    ("anthropic/claude-sonnet-4-5", "deepseek/deepseek-chat"),
    ("openai/gpt-4o", "anthropic/claude-sonnet-4-5"),
]

_paid_router_models = [
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
    return os.environ.get("ASSURE_USE_FREE_MODELS", "0").strip().lower() in ("1", "true", "yes")


def get_compare_pair(index: int = 0) -> tuple[str, str]:
    pairs = FREE_MODEL_PAIRS if use_free_models() else PRODUCTION_MODEL_PAIRS
    return pairs[index % len(pairs)]


def get_active_model_stack() -> str:
    return "free" if use_free_models() else "production"


def display_name_for_model(model: str) -> str:
    slug = str(model or "").split("/")[-1]
    labels = {
        "gemini-3.6-flash": "Gemini 3.6 Flash",
        "gemini-2.5-flash": "Gemini 2.5 Flash",
        "gemini-2.0-flash": "Gemini 2.0 Flash",
        "deepseek-chat": "DeepSeek V3",
        "deepseek-reasoner": "DeepSeek Reasoner",
        "llama-3.3-70b-versatile": "Llama 3.3 70B",
        "claude-sonnet-4-5": "Claude Sonnet 4.5",
        "gpt-4o": "GPT-4o",
    }
    return labels.get(slug, slug.replace("-", " ").title())


def orchestrator_model_pairs() -> dict[str, dict[str, str]]:
    """UI slot keys (claude/deepseek) → model metadata for /health."""
    model_a, model_b = get_compare_pair()
    return {
        "claude": {
            "name": display_name_for_model(model_a),
            "litellm_model": model_a,
            "provider": model_a.split("/")[0],
        },
        "deepseek": {
            "name": display_name_for_model(model_b),
            "litellm_model": model_b,
            "provider": model_b.split("/")[0],
        },
    }


def _gemini_api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def _build_model_list() -> list[dict[str, Any]]:
    if not use_free_models():
        return _paid_router_models
    model_a, model_b = get_compare_pair()
    gemini = model_a if model_a.startswith("gemini/") else model_b
    deepseek = model_b if model_b.startswith("deepseek/") else model_a
    if not gemini.startswith("gemini/"):
        gemini = "gemini/gemini-3.6-flash"
    if not deepseek.startswith("deepseek/"):
        deepseek = "deepseek/deepseek-chat"
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
            "litellm_params": {"model": gemini, "api_key": _gemini_api_key()},
        },
        {
            "model_name": "vision-analysis",
            "litellm_params": {"model": gemini, "api_key": _gemini_api_key()},
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
