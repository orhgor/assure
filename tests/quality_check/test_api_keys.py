"""API key and external service audit — presence checks; live calls are opt-in."""

from __future__ import annotations

import os

import pytest
import requests

ALWAYS_REQUIRED = [
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
]

STAGING_EXTRA = [
    "RESEND_FROM_EMAIL",
    "FEEDBACK_EMAIL",
]

PRODUCTION_ONLY = [
    "OPENAI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_REGION",
    "RESEND_API_KEY",
    "SENTRY_DSN",
]

OPTIONAL = [
    "PERPLEXITY_API_KEY",
]

ENV = os.environ.get("QUALITY_CHECK_ENV", "staging").lower()

if ENV == "production":
    REQUIRED = ALWAYS_REQUIRED + STAGING_EXTRA + PRODUCTION_ONLY
elif ENV == "staging":
    REQUIRED = ALWAYS_REQUIRED + STAGING_EXTRA
else:
    REQUIRED = ALWAYS_REQUIRED


def _key_value(key: str) -> str:
    if key == "ANTHROPIC_API_KEY":
        return (
            os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY") or ""
        ).strip()
    return (os.environ.get(key) or "").strip()


def audit_key_status() -> dict[str, str]:
    """Return set / missing / invalid per key without printing secret values."""
    report: dict[str, str] = {}
    for key in REQUIRED + OPTIONAL:
        raw = _key_value(key)
        if not raw:
            report[key] = "missing"
        elif raw.lower() in {"changeme", "placeholder", "your-key-here", "xxx"}:
            report[key] = "invalid"
        elif len(raw) < 8 and key not in {"AWS_REGION", "RESEND_FROM_EMAIL", "FEEDBACK_EMAIL"}:
            report[key] = "invalid"
        else:
            report[key] = "set"
    return report


pytestmark = pytest.mark.skipif(
    not any(_key_value(k) for k in ALWAYS_REQUIRED),
    reason="No provider keys in env — source .env.staging or .env.production before strict audit",
)


def test_api_key_audit_report():
    report = audit_key_status()
    missing = [k for k in REQUIRED if report.get(k) == "missing"]
    invalid = [k for k in REQUIRED if report.get(k) == "invalid"]
    assert not invalid, f"Invalid placeholder keys: {invalid}"
    if os.environ.get("QUALITY_CHECK_STRICT") == "1":
        assert not missing, f"Missing required keys for env={ENV}: {missing}"
    else:
        print(f"API key audit (env={ENV}):", report)


@pytest.mark.parametrize("key", REQUIRED)
def test_required_key_present(key: str):
    if os.environ.get("QUALITY_CHECK_STRICT") != "1":
        pytest.skip("Strict key check — set QUALITY_CHECK_STRICT=1 after sourcing env")
    value = _key_value(key)
    assert value, f"{key} is required for env={ENV}"
    if key not in {"AWS_REGION", "RESEND_FROM_EMAIL", "FEEDBACK_EMAIL"}:
        assert value.lower() not in {"changeme", "placeholder", "your-key-here", "xxx"}


@pytest.mark.skipif(
    os.environ.get("QUALITY_CHECK_LIVE_CALLS") != "1",
    reason="Set QUALITY_CHECK_LIVE_CALLS=1 to hit live APIs",
)
def test_deepseek_api_reachable():
    api_key = _key_value("DEEPSEEK_API_KEY")
    assert api_key
    response = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        },
        timeout=30,
    )
    assert response.status_code in (200, 402, 429), response.text[:200]


@pytest.mark.skipif(
    os.environ.get("QUALITY_CHECK_LIVE_CALLS") != "1",
    reason="Set QUALITY_CHECK_LIVE_CALLS=1 to hit live APIs",
)
def test_anthropic_api_reachable():
    api_key = _key_value("ANTHROPIC_API_KEY")
    assert api_key
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": "claude-3-5-haiku-20241022",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        },
        timeout=30,
    )
    assert response.status_code in (200, 402, 429), response.text[:200]


@pytest.mark.skipif(
    os.environ.get("QUALITY_CHECK_LIVE_CALLS") != "1",
    reason="Set QUALITY_CHECK_LIVE_CALLS=1 to hit live APIs",
)
def test_gemini_api_reachable():
    api_key = _key_value("GEMINI_API_KEY")
    assert api_key
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}",
        json={
            "contents": [{"parts": [{"text": "ping"}]}],
            "generationConfig": {"maxOutputTokens": 1},
        },
        timeout=30,
    )
    assert response.status_code in (200, 402, 429), response.text[:200]
