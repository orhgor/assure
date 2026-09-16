"""Verified provider pricing. Update when rates change.

Source: OpenRouter, verified 2026-09-16.
All prices are USD per million tokens.
"""

from __future__ import annotations

PRICES: dict[str, dict[str, float | None]] = {
    "anthropic/claude-sonnet-4.5": {"in": 3.00, "out": 15.00, "cache_read": 0.30},
    "deepseek/deepseek-v4-flash-0731": {"in": 0.05, "out": 0.10, "cache_read": 0.0028},
    "google/gemini-2.0-flash": {"in": 0.10, "out": 0.40, "cache_read": None},
    # Id aliases — same validated models, different spellings used by this
    # codebase (policy.litellm_model / draft DRAFT_MODEL). Rates are NOT new;
    # they reuse the verified rows above for the same underlying model.
    "anthropic/claude-sonnet-4-5": {"in": 3.00, "out": 15.00, "cache_read": 0.30},
    "gemini/gemini-2.0-flash": {"in": 0.10, "out": 0.40, "cache_read": None},
}


def compute_usd(
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read: int = 0,
) -> float | None:
    """Return USD cost, or None if the model is unknown."""
    p = PRICES.get(model)
    if not p:
        return None
    nc = max(0, (input_tokens or 0) - (cache_read or 0))
    cache_rate = p["cache_read"] if p["cache_read"] is not None else p["in"]
    return (nc * p["in"] + cache_read * cache_rate + (output_tokens or 0) * p["out"]) / 1_000_000
