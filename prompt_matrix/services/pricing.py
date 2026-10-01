"""Provider pricing. Update when rates change.

Hosted rows: OpenRouter's published rates, verified 2026-09-16 (kept for
explicit ``target_ai`` overrides; OpenRouter itself was removed 2026-10-01).
Bedrock rows (2026-10-01): Anthropic's list rates for Sonnet 5.5 ($3 / $15)
and Opus 5.5 ($5 / $25), which Bedrock on-demand in us-east-1 has matched for
every Claude generation. NOT verified against the AWS Price List API — the
lookup failed with an invalid-token error on 2026-10-01; re-check them with
``AmazonBedrockFoundationModels`` when credentials allow.
All prices are USD per million tokens.
"""

from __future__ import annotations

PRICES: dict[str, dict[str, float | None]] = {
    "anthropic/claude-sonnet-4.5": {"in": 3.00, "out": 15.00, "cache_read": 0.30},
    "google/gemini-2.0-flash": {"in": 0.10, "out": 0.40, "cache_read": None},
    # OpenRouter :floor (cheapest available provider, measured 2026-09-17:
    # DeepInfra $0.075/$0.25 per 1M). model_id spelling the policies report.
    "z-ai/glm-5.3-flash": {"in": 0.075, "out": 0.25, "cache_read": None},
    # Id aliases — same validated models, different spellings used by this
    # codebase (policy.litellm_model). Rates are NOT new; they reuse the
    # verified rows above for the same underlying model.
    "anthropic/claude-sonnet-4-5": {"in": 3.00, "out": 15.00, "cache_read": 0.30},
    "gemini/gemini-2.0-flash": {"in": 0.10, "out": 0.40, "cache_read": None},
}

# Bedrock (default backend since 2026-10-01): keyed by every spelling the ledger
# sees — the litellm id sent (`bedrock/us.…`), the inference-profile id, and the
# bare foundation-model id an ASSURE_BEDROCK_MODEL_* override may carry.
_BEDROCK_RATES: dict[str, dict[str, float | None]] = {
    "anthropic.claude-sonnet-5-5": {"in": 3.00, "out": 15.00, "cache_read": 0.30},
    "anthropic.claude-opus-5-5": {"in": 5.00, "out": 25.00, "cache_read": 0.50},
}
for _bare, _rate in _BEDROCK_RATES.items():
    for _id in (_bare, f"us.{_bare}", f"bedrock/{_bare}", f"bedrock/us.{_bare}"):
        PRICES[_id] = dict(_rate)


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
