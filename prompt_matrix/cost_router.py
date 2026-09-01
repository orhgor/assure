"""Estimate send cost and pick a cheaper live model when asked.

MODEL_PRICING figures are the table shipped with this module (USD per 1M tokens).
They are not a live vendor quote. Update the table when you care about accuracy.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

try:
    from .token_counter import count_tokens
except ImportError:
    from token_counter import count_tokens

# Pricing per 1M input / output tokens (USD) — update regularly
MODEL_PRICING = {
    "claude-3-5-sonnet-20240620": {"input": 3.00, "output": 15.00},
    "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
    "gemini-1.5-pro": {"input": 2.50, "output": 7.50},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    "kimi-moonshot-v1": {"input": 0.50, "output": 1.50},  # approximate
}

# Routing rules: (max_input_tokens, min_required_reasoning) -> preferred_model
ROUTING_RULES = [
    # If input is tiny (< 2k) and intent is simple -> cheapest flash model
    {"max_tokens": 2048, "intents": ["debug", "comparison"], "prefer": "gemini-1.5-flash"},
    # If input is medium (< 20k) and intent is analytical -> DeepSeek (cheap + good reasoning)
    {"max_tokens": 20000, "intents": ["analysis", "design"], "prefer": "deepseek-chat"},
    # Long documents (> 20k) but not heavy reasoning -> Haiku (cheap for long context)
    {"max_tokens": 100000, "intents": ["research"], "prefer": "claude-3-haiku-20240307"},
    # Heavy lifting (research + long docs) -> best-in-class reasoning (Pro/Sonnet)
    {"max_tokens": 1000000, "intents": ["research"], "prefer": "claude-3-5-sonnet-20240620"},
]

# PEM target / LiteLLM ids → a row in MODEL_PRICING
MODEL_ALIASES = {
    "claude": "claude-3-5-sonnet-20240620",
    "sonnet": "claude-3-5-sonnet-20240620",
    "haiku": "claude-3-haiku-20240307",
    "gemini": "gemini-1.5-flash",
    "flash": "gemini-1.5-flash",
    "pro": "gemini-1.5-pro",
    "deepseek": "deepseek-chat",
    "kimi": "kimi-moonshot-v1",
    "moonshot": "kimi-moonshot-v1",
    "anthropic/claude-sonnet-4-5": "claude-3-5-sonnet-20240620",
    "anthropic/claude-3-5-sonnet-20240620": "claude-3-5-sonnet-20240620",
    "anthropic/claude-3-haiku-20240307": "claude-3-haiku-20240307",
    "gemini/gemini-3.6-flash": "gemini-1.5-flash",
    "gemini/gemini-3.5-flash": "gemini-1.5-flash",
    "gemini/gemini-3.5-flash-lite": "gemini-1.5-flash",
    "gemini/gemini-3.7-flash": "gemini-1.5-flash",
    "gemini/gemini-1.5-flash": "gemini-1.5-flash",
    "gemini/gemini-1.5-pro": "gemini-1.5-pro",
    "gemini/gemini-2.5-flash": "gemini-1.5-flash",
    "gemini/gemini-2.5-pro": "gemini-1.5-pro",
    "deepseek/deepseek-chat": "deepseek-chat",
    "moonshot/kimi-k2.5": "kimi-moonshot-v1",
}

TARGET_FOR = {
    "gemini-1.5-flash": "gemini",
    "gemini-1.5-pro": "gemini",
    "deepseek-chat": "deepseek",
    "claude-3-haiku-20240307": "claude",
    "claude-3-5-sonnet-20240620": "claude",
    "kimi-moonshot-v1": "kimi",
}

LITELLM_FOR = {
    "gemini-1.5-flash": "gemini/gemini-3.5-flash-lite",
    "gemini-1.5-pro": "gemini/gemini-3.5-flash",
    "deepseek-chat": "deepseek/deepseek-chat",
    "claude-3-haiku-20240307": "anthropic/claude-3-haiku-20240307",
    "claude-3-5-sonnet-20240620": "anthropic/claude-sonnet-4-5",
    "kimi-moonshot-v1": "moonshot/kimi-k2.5",
}

# Gemini 1.5 is retired on v1beta. gemini-3.6-flash is a separate quota pool that
# 429s after the free generateContent cap. These ids still Send on this key.
SEND_REWRITES = {
    "gemini/gemini-1.5-pro": "gemini/gemini-3.5-flash",
    "gemini/gemini-1.5-flash": "gemini/gemini-3.5-flash-lite",
    "gemini/gemini-2.0-flash": "gemini/gemini-3.5-flash",
    "gemini/gemini-2.5-flash": "gemini/gemini-3.5-flash-lite",
    "gemini/gemini-2.5-pro": "gemini/gemini-3.5-flash",
    "gemini/gemini-3.6-flash": "gemini/gemini-3.5-flash",
}

_model_override: ContextVar[tuple[str, str] | None] = ContextVar("pem_cost_model", default=None)
_output_token_floor: ContextVar[int | None] = ContextVar("pem_output_token_floor", default=None)
_timeout_floor: ContextVar[int | None] = ContextVar("pem_timeout_floor", default=None)


def cost_route_enabled() -> bool:
    return os.environ.get("PEM_COST_ROUTE", "").strip().lower() in {"1", "true", "yes", "on", "cheap"}


def should_auto_route(*, local: bool = False, cheap: bool = False) -> bool:
    if local:
        return False
    if (os.environ.get("PEM_MODEL") or "").strip():
        return False
    return cheap or cost_route_enabled()


def routed_model(target_ai: str | None = None) -> str | None:
    pair = _model_override.get()
    if not pair:
        return None
    target, model_id = pair
    if target_ai and target != target_ai:
        return None
    return model_id


@contextmanager
def model_override(target: str | None, model_id: str | None = None) -> Iterator[None]:
    if not target or not model_id:
        yield
        return
    token = _model_override.set((target, model_id))
    try:
        yield
    finally:
        _model_override.reset(token)


@contextmanager
def role_output_limits(
    *,
    max_tokens: int | None = None,
    timeout: int | None = None,
) -> Iterator[None]:
    """Raise the LiteLLM output cap for one role (swarm developer dumps)."""
    if max_tokens is None and timeout is None:
        yield
        return
    tok = _output_token_floor.set(int(max_tokens) if max_tokens else None)
    sec = _timeout_floor.set(int(timeout) if timeout else None)
    try:
        yield
    finally:
        _output_token_floor.reset(tok)
        _timeout_floor.reset(sec)


def current_role_limits() -> tuple[int | None, int | None]:
    return _output_token_floor.get(), _timeout_floor.get()


def _pricing_key(model: str | None) -> str | None:
    raw = (model or "").strip()
    if not raw:
        return None
    if raw in MODEL_PRICING:
        return raw
    lowered = raw.lower()
    if lowered in MODEL_PRICING:
        return lowered
    if lowered in MODEL_ALIASES:
        return MODEL_ALIASES[lowered]
    if "/" in raw:
        suffix = raw.split("/", 1)[1]
        if suffix in MODEL_PRICING:
            return suffix
        if suffix.lower() in MODEL_ALIASES:
            return MODEL_ALIASES[suffix.lower()]
    return None


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Returns estimated cost in USD."""
    lowered = (model or "").strip().lower()
    if not lowered or lowered == "cursor" or lowered.startswith("ollama/") or lowered == "ollama":
        return 0.0
    key = _pricing_key(model)
    pricing = MODEL_PRICING.get(key or "", {"input": 1.00, "output": 1.00})  # fallback
    return (input_tokens / 1_000_000) * pricing["input"] + (output_tokens / 1_000_000) * pricing["output"]


def rewrite_send_id(model_id: str | None) -> str | None:
    """Replace retired or quota-exhausted Gemini ids with a live Send id."""
    if not model_id:
        return model_id
    return SEND_REWRITES.get(model_id, model_id)


def send_model_id(preferred: str, fallback: str | None = None) -> str:
    """Map a pricing-table key (or alias) to a LiteLLM id."""
    key = _pricing_key(preferred) or preferred
    raw = LITELLM_FOR.get(key) or fallback or preferred
    return rewrite_send_id(raw) or raw


def suggest_optimal_model(prompt_text: str, intent: str, user_preference: str = None) -> str:
    """
    Returns the cheapest model that fits the prompt length and intent.
    If user_preference is set (e.g., --model claude), respects it.
    """
    if user_preference:
        key = _pricing_key(user_preference)
        if key:
            return key  # respect explicit override
        return user_preference

    input_tokens = count_tokens(prompt_text)
    intent_name = (intent or "").strip().lower()
    for rule in ROUTING_RULES:
        if input_tokens <= rule["max_tokens"] and intent_name in rule["intents"]:
            return rule["prefer"]

    # Fallback: if no rule matches, use the cheapest overall
    return min(MODEL_PRICING, key=lambda m: MODEL_PRICING[m]["input"])


def suggest_live_target(
    prompt_text: str,
    intent: str,
    live: list[str],
    user_preference: str | None = None,
) -> tuple[str, str | None, str]:
    """Pick a PEM target that is up. Returns (target, litellm id, note)."""
    key = suggest_optimal_model(prompt_text, intent, user_preference)
    target = TARGET_FOR.get(key, key)
    model = LITELLM_FOR.get(key)
    if live and target not in live:
        ranked = sorted(MODEL_PRICING, key=lambda m: MODEL_PRICING[m]["input"])
        picked = None
        for row in ranked:
            cand = TARGET_FOR.get(row)
            if cand in live:
                picked = row
                break
        if picked is None:
            target = live[0]
            model = None
            key = target
        else:
            key = picked
            target = TARGET_FOR[picked]
            model = LITELLM_FOR.get(picked)
    return target, model, f"Cost route picked {target} ({key})."


SHORT_PROMPT_TOKENS = 1000
# High list-price rows plus any *opus* id. Used only to drop ensemble extras on short prompts.
EXPENSIVE_ON_SHORT = frozenset({
    "claude-3-5-sonnet-20240620",
    "gemini-1.5-pro",
})


def is_cost_inefficient_for_short(model: str, prompt_text: str) -> bool:
    """True when a short prompt should not spend a frontier / Opus-class model."""
    if count_tokens(prompt_text) >= SHORT_PROMPT_TOKENS:
        return False
    raw = (model or "").lower()
    if "opus" in raw:
        return True
    key = _pricing_key(model)
    return key in EXPENSIVE_ON_SHORT


def filter_ensemble_extras(primary: str, extras: list[str], prompt_text: str) -> tuple[list[str], list[str]]:
    """Keep the ensemble. Drop extras that are a bad fit for a short prompt."""
    kept: list[str] = []
    skipped: list[str] = []
    for name in extras:
        if name == primary:
            continue
        if is_cost_inefficient_for_short(name, prompt_text):
            skipped.append(name)
        else:
            kept.append(name)
    return kept, skipped


def cap_output_tokens(intent: str, model: str) -> int:
    """Dynamic output cap based on intent and model capability."""
    base_cap = {
        "debug": 512,
        "comparison": 1024,
        "design": 1536,
        "analysis": 2048,
        "research": 4096,
    }.get((intent or "").strip().lower(), 2048)

    name = (model or "").lower()
    key = _pricing_key(model)
    mapped = (LITELLM_FOR.get(key or "", "") or "").lower()
    probe = f"{name} {mapped} {key or ''}"
    # Gemini 2.5/3 count hidden thinking against max_tokens. Pricing aliases
    # like gemini-1.5-flash still send gemini/gemini-3.5-flash-lite.
    if any(tag in probe for tag in ("gemini-2.5", "gemini-3", "thinking")):
        capped = max(base_cap, 8192)
    elif "flash" in name or "haiku" in name:
        capped = min(base_cap, 1024)
    else:
        capped = base_cap
    floor = _output_token_floor.get()
    if floor:
        return max(capped, floor)
    return capped
