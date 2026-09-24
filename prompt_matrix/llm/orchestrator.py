"""LiteLLM Router + Difference Engine model-pair selection (staging free stack)."""

from __future__ import annotations

import logging
import os
from typing import Any

try:
    from litellm import Router
except ImportError:  # pragma: no cover
    Router = None  # type: ignore[misc, assignment]

try:
    from ..keys import ORCHESTRATOR_ENV_MAP, provider_slug_for_litellm
except ImportError:
    from keys import ORCHESTRATOR_ENV_MAP, provider_slug_for_litellm

log = logging.getLogger(__name__)

# Fail fast if someone reintroduces a divergent env_map in this module.
assert set(ORCHESTRATOR_ENV_MAP) == {
    "claude",
    "gemini",
    "groq",
    "openrouter",
}

# Longer keys first — family_of() scans with substring match.
FAMILY_PREFIXES: dict[str, str] = {
    "meta-llama": "meta",
    "mistralai": "mistral",
    "microsoft": "microsoft",
    "nemotron": "nvidia",
    "nvidia": "nvidia",
    "nex-agi": "nex",
    "poolside": "poolside",
    "inclusionai": "inclusionai",
    "thinkingmachines": "thinkingmachines",
    "dots-studio": "dots",
    "openrouter": "openrouter",  # only if no vendor remains after strip
    "liquid": "liquid",
    "cohere": "cohere",
    "google": "google",
    "gemma": "google",
    "mistral": "mistral",
    "qwen": "qwen",
    "llama": "meta",
    "phi": "microsoft",
    "groq": "meta",
}

# OpenRouter-only free stack (no Groq — Cloudflare 403/1010 from AWS EC2).
# Each pair must be two DIFFERENT families so the Difference Engine can diverge.
FREE_MODEL_A = "openrouter/google/gemma-4-26b-a4b-it:free"
FREE_MODEL_B = "openrouter/nvidia/nemotron-3.5-lightning:free"

FREE_MODEL_PAIRS: list[tuple[str, str]] = [
    (FREE_MODEL_A, FREE_MODEL_B),  # google vs nvidia
    (
        "openrouter/nex-agi/nex-n2.5-mini:free",
        "openrouter/nvidia/nemotron-3.5-lightning:free",
    ),  # nex vs nvidia
    (
        "openrouter/liquid/lfm-2.5-2.6b:free",
        "openrouter/google/gemma-4-31b-it:free",
    ),  # liquid vs google
    (
        "openrouter/poolside/laguna-xs-2.1:free",
        "openrouter/nvidia/nemotron-3.5-lightning:free",
    ),  # poolside vs nvidia
]

PRODUCTION_MODEL_PAIRS: list[tuple[str, str]] = [
    ("anthropic/claude-sonnet-4-5", "openrouter/qwen/qwen3-next-80b-a3b-instruct"),
    ("openai/gpt-4o", "anthropic/claude-sonnet-4-5"),
]

PROVIDER_TIMEOUTS: dict[str, int] = {
    "ollama": 600,  # CPU inference in a container: a 3B model streams ~5–15 tok/s
    "openrouter": 180,
    "groq": 180,
    "llm7": 180,
    "huggingface": 180,
    "cloudflare": 180,
    "gemini": 60,
    "openai": 60,
    "anthropic": 60,
}

_paid_router_models = [
    {
        "model_name": "text-reasoning",
        "litellm_params": {
            "model": "openrouter/qwen/qwen3-next-80b-a3b-instruct",
            "api_key": os.getenv("OPENROUTER_API_KEY"),
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


def family_of(slug: str) -> str:
    """Detect model family from a LiteLLM / OpenRouter slug."""
    s = str(slug or "").lower()
    # Prefer the vendor segment after openrouter/
    probe = s
    if probe.startswith("openrouter/"):
        probe = probe[len("openrouter/") :]
    for key, fam in FAMILY_PREFIXES.items():
        if key == "openrouter":
            continue
        if key in probe:
            return fam
    return "unknown"


def select_free_pair() -> tuple[str, str]:
    """Return the first free pair whose models belong to different families."""
    for model_a, model_b in FREE_MODEL_PAIRS:
        if family_of(model_a) != family_of(model_b):
            return model_a, model_b
    raise RuntimeError(
        "No free model pair has two different families. Diff engine cannot produce divergence."
    )


def free_pairs_different_families() -> list[tuple[str, str]]:
    """All free pairs that qualify for the Difference Engine."""
    return [
        (model_a, model_b)
        for model_a, model_b in FREE_MODEL_PAIRS
        if family_of(model_a) != family_of(model_b)
    ]


def timeout_for(model_slug: str) -> int:
    s = str(model_slug or "").lower()
    for key, seconds in PROVIDER_TIMEOUTS.items():
        if key in s:
            return seconds
    return 120


def get_compare_pair(index: int = 0) -> tuple[str, str]:
    """Compare's two models. Local backend: the two Ollama models
    (``ASSURE_OLLAMA_MODEL`` / ``ASSURE_OLLAMA_MODEL_B``); otherwise the
    free or production pair."""
    try:
        from ..cost_governance import llm_backend, local_model
    except ImportError:
        from cost_governance import llm_backend, local_model
    if llm_backend() == "ollama":
        return local_model("a"), local_model("b")
    return _cloud_compare_pair(index)


def _cloud_compare_pair(index: int = 0) -> tuple[str, str]:
    if use_free_models():
        pairs = free_pairs_different_families()
        if not pairs:
            raise RuntimeError(
                "No free model pair has two different families. "
                "Diff engine cannot produce divergence."
            )
        return pairs[index % len(pairs)]
    pairs = PRODUCTION_MODEL_PAIRS
    return pairs[index % len(pairs)]


def get_active_model_stack() -> str:
    return "free" if use_free_models() else "production"


def display_name_for_model(model: str) -> str:
    slug = str(model or "").split("/")[-1]
    labels = {
        "gemini-3.6-flash": "Gemini 3.6 Flash",
        "gemini-2.5-flash": "Gemini 2.5 Flash",
        "gemini-2.0-flash": "Gemini 2.0 Flash",
        "llama-3.3-70b-versatile": "Llama 3.3 70B (Groq)",
        "nemotron-3.5-lightning:free": "Nemotron 3.5 Lightning (OpenRouter)",
        "nex-n2.5-mini:free": "Nex N2.5 Mini (OpenRouter)",
        "lfm-2.5-2.6b:free": "LFM 2.5 (OpenRouter)",
        "laguna-xs-2.1:free": "Laguna XS (OpenRouter)",
        "gemma-4-26b-a4b-it:free": "Gemma 4 26B (OpenRouter)",
        "gemma-4-31b-it:free": "Gemma 4 31B (OpenRouter)",
        "claude-sonnet-4-5": "Claude Sonnet 4.5",
        "gpt-4o": "GPT-4o",
    }
    return labels.get(slug, slug.replace("-", " ").replace(":", " ").title())


def orchestrator_model_pairs() -> dict[str, dict[str, str]]:
    """UI slot keys (claude/secondary) → model metadata for /health."""
    model_a, model_b = get_compare_pair()
    return {
        "claude": {
            "name": display_name_for_model(model_a),
            "litellm_model": model_a,
            "provider": model_a.split("/")[0],
            "family": family_of(model_a),
        },
        "secondary": {
            "name": display_name_for_model(model_b),
            "litellm_model": model_b,
            "provider": model_b.split("/")[0],
            "family": family_of(model_b),
        },
    }


def _api_key_env_for_model(model: str) -> str | None:
    slug = provider_slug_for_litellm(model)
    env_name = ORCHESTRATOR_ENV_MAP.get(slug) if slug else None
    if not env_name:
        return None
    if slug == "gemini":
        return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if slug == "claude":
        return os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
    return os.getenv(env_name)


def _litellm_params_for(model: str, *, max_tokens: int | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "model": model,
        "api_key": _api_key_env_for_model(model),
        "timeout": timeout_for(model),
    }
    if max_tokens is not None:
        params["max_tokens"] = max_tokens
    if provider_slug_for_litellm(model) == "openrouter":
        params["api_base"] = os.getenv("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1")
        params["extra_headers"] = {
            "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "https://staging.getassureai.com"),
            "X-Title": os.getenv("OPENROUTER_APP_TITLE", "Assure AI"),
        }
    return params


def _build_model_list() -> list[dict[str, Any]]:
    if not use_free_models():
        return _paid_router_models
    seen: set[str] = set()
    entries: list[dict[str, Any]] = []
    for model_a, model_b in free_pairs_different_families() or FREE_MODEL_PAIRS:
        for model in (model_a, model_b):
            if model in seen:
                continue
            seen.add(model)
            entries.append(
                {
                    "model_name": model,
                    "litellm_params": _litellm_params_for(model, max_tokens=4096),
                }
            )
    default_a, default_b = get_compare_pair()
    entries.extend(
        [
            {
                "model_name": "text-reasoning",
                "litellm_params": _litellm_params_for(default_a, max_tokens=4096),
            },
            {
                "model_name": "table-parsing",
                "litellm_params": _litellm_params_for(default_b),
            },
            {
                "model_name": "vision-analysis",
                "litellm_params": _litellm_params_for(default_a),
            },
        ]
    )
    return entries


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


def log_free_pair_at_startup() -> None:
    """Print the selected free pair when ASSURE_USE_FREE_MODELS=1."""
    if not use_free_models():
        return
    try:
        model_a, model_b = select_free_pair()
        print(
            f"[orchestrator] free pair: {model_a} ({family_of(model_a)}) "
            f"vs {model_b} ({family_of(model_b)})",
            flush=True,
        )
    except Exception as exc:
        print(f"[orchestrator] free pair selection failed: {exc}", flush=True)
        raise
