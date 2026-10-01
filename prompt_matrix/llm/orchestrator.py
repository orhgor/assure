"""LiteLLM Router + Difference Engine model-pair selection (Bedrock / Ollama)."""

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

# Fail fast if someone reintroduces a divergent env_map in this module. Bedrock
# and Ollama are absent on purpose: neither takes an API key (boto3 credential
# chain / local container). OpenRouter was removed on 2026-10-01 (user decision).
assert set(ORCHESTRATOR_ENV_MAP) == {
    "claude",
    "gemini",
    "groq",
}

# Longer keys first — family_of() scans with substring match.
FAMILY_PREFIXES: dict[str, str] = {
    "meta-llama": "meta",
    "mistralai": "mistral",
    "microsoft": "microsoft",
    "nemotron": "nvidia",
    "nvidia": "nvidia",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "cohere": "cohere",
    "google": "google",
    "gemini": "google",
    "gemma": "google",
    "mistral": "mistral",
    "qwen": "qwen",
    "llama": "meta",
    "phi": "microsoft",
    "groq": "meta",
}

# The OpenRouter `:free` Difference-Engine stack (ASSURE_USE_FREE_MODELS=1,
# staging since 2026-09-10) was removed with OpenRouter on 2026-10-01: every
# pair was an `openrouter/…:free` id. Compare now runs the configured backend's
# two columns — Bedrock: bedrock_model("a") / bedrock_model("b"), both Claude
# Sonnet 5.5 by default; Ollama: the draft and compare models.
#: The default Bedrock pair, for display and tests; get_compare_pair() reads
#: the per-role env overrides at call time.
PRODUCTION_MODEL_PAIRS: list[tuple[str, str]] = [
    ("bedrock/us.anthropic.claude-sonnet-5-5", "bedrock/us.anthropic.claude-sonnet-5-5"),
]

PROVIDER_TIMEOUTS: dict[str, int] = {
    "ollama": 600,  # CPU inference in a container: a 3B model streams ~5–15 tok/s
    "bedrock": 180,
    "groq": 180,
    "llm7": 180,
    "huggingface": 180,
    "cloudflare": 180,
    "gemini": 60,
    "openai": 60,
    "anthropic": 60,
}

_assure_router: Any | None = None


def use_free_models() -> bool:
    """Always ``False``: the free stack was OpenRouter-only and went with it on
    2026-10-01. A leftover ``ASSURE_USE_FREE_MODELS=1`` in an old .env is
    ignored rather than routing Compare to ids nothing can serve."""
    return False


def family_of(slug: str) -> str:
    """Detect model family from a LiteLLM slug (``bedrock/us.anthropic.…`` →
    ``anthropic``)."""
    s = str(slug or "").lower()
    for key, fam in FAMILY_PREFIXES.items():
        if key in s:
            return fam
    return "unknown"


def timeout_for(model_slug: str) -> int:
    s = str(model_slug or "").lower()
    for key, seconds in PROVIDER_TIMEOUTS.items():
        if key in s:
            return seconds
    return 120


def get_compare_pair(index: int = 0) -> tuple[str, str]:
    """Compare's two models on the configured backend. Bedrock (default):
    ``bedrock_model("a")`` / ``bedrock_model("b")``; Ollama: the draft and
    compare models. ``index`` is kept for callers that rotated through the
    retired free pairs; there is one pair per backend."""
    try:
        from ..cost_governance import bedrock_model, llm_backend, local_model
    except ImportError:
        from cost_governance import bedrock_model, llm_backend, local_model
    if llm_backend() == "ollama":
        return local_model("a"), local_model("b")
    return bedrock_model("a"), bedrock_model("b")


def get_active_model_stack() -> str:
    return "production"


_BEDROCK_DISPLAY = {
    "anthropic.claude-sonnet-5-5": "Claude Sonnet 5.5 (Bedrock)",
    "anthropic.claude-opus-5-5": "Claude Opus 5.5 (Bedrock)",
}


def display_name_for_model(model: str) -> str:
    slug = str(model or "").split("/")[-1]
    # Bedrock ids carry the inference-profile prefix (`us.anthropic.…`).
    bare = slug.split(".", 1)[1] if slug.startswith(("us.", "eu.", "apac.", "global.")) else slug
    if bare in _BEDROCK_DISPLAY:
        return _BEDROCK_DISPLAY[bare]
    labels = {
        "gemini-3.6-flash": "Gemini 3.6 Flash",
        "gemini-2.5-flash": "Gemini 2.5 Flash",
        "gemini-2.0-flash": "Gemini 2.0 Flash",
        "llama-3.3-70b-versatile": "Llama 3.3 70B (Groq)",
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
        "timeout": timeout_for(model),
    }
    slug = provider_slug_for_litellm(model)
    if slug in ("bedrock", "ollama"):
        # Keyless: Bedrock gets the region (boto3 signs with the task role or
        # the .env AWS_* keys), Ollama the container's api_base.
        try:
            from ..keys import litellm_kwargs_for
        except ImportError:
            from keys import litellm_kwargs_for
        params.update(litellm_kwargs_for(slug))
    else:
        params["api_key"] = _api_key_env_for_model(model)
    if max_tokens is not None:
        params["max_tokens"] = max_tokens
    return params


def _build_model_list() -> list[dict[str, Any]]:
    """The Router's three task names, all on the configured backend.

    Before 2026-10-01 ``text-reasoning`` was an OpenRouter Qwen id and the other
    two needed Gemini / Anthropic keys, so refine_node and compile_tasks failed
    on a Bedrock-only deployment. Now: text and vision → the draft model
    (``bedrock_model("a")``), tables → the analysis model."""
    try:
        from ..cost_governance import resolve_model
    except ImportError:
        from cost_governance import resolve_model
    text_model = resolve_model("", role="a")
    table_model = resolve_model("", role="analysis")
    return [
        {"model_name": "text-reasoning", "litellm_params": _litellm_params_for(text_model, max_tokens=4096)},
        {"model_name": "table-parsing", "litellm_params": _litellm_params_for(table_model)},
        {"model_name": "vision-analysis", "litellm_params": _litellm_params_for(text_model)},
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
