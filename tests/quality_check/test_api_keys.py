"""API key and external service audit — presence checks; live calls are opt-in."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_KEYS = [
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_REGION",
    "RESEND_API_KEY",
    "RESEND_FROM_EMAIL",
    "FEEDBACK_EMAIL",
    "SENTRY_DSN",
]

ENV_CANDIDATES = (
    ROOT / "prompt_matrix" / ".env",
    ROOT / ".env",
    ROOT / ".env.production",
)


def load_env() -> dict[str, str | None]:
    merged: dict[str, str | None] = {}
    for path in ENV_CANDIDATES:
        if path.is_file():
            merged.update(dotenv_values(path))
    for key, value in os.environ.items():
        if value:
            merged[key] = value
    return merged


def _env_available() -> bool:
    if os.environ.get("QUALITY_CHECK_STRICT") == "1":
        return True
    return any(path.is_file() for path in ENV_CANDIDATES)


def audit_key_status() -> dict[str, str]:
    """Return set / missing / invalid per key without printing secret values."""
    env = load_env()
    report: dict[str, str] = {}
    for key in REQUIRED_KEYS:
        raw = (env.get(key) or os.environ.get(key) or "").strip()
        if not raw:
            report[key] = "missing"
        elif raw.lower() in {"changeme", "placeholder", "your-key-here", "xxx"}:
            report[key] = "invalid"
        elif len(raw) < 8:
            report[key] = "invalid"
        else:
            report[key] = "set"
    return report


pytestmark = pytest.mark.skipif(
    not _env_available(),
    reason="No .env found — set QUALITY_CHECK_STRICT=1 on self-hosted runner with secrets",
)


def test_api_key_audit_report():
    report = audit_key_status()
    missing = [k for k, v in report.items() if v == "missing"]
    invalid = [k for k, v in report.items() if v == "invalid"]
    assert not invalid, f"Invalid placeholder keys: {invalid}"
    if os.environ.get("QUALITY_CHECK_STRICT") == "1":
        assert not missing, f"Missing required keys: {missing}"
    else:
        # Advisory mode: always emit a readable summary for CI logs.
        print("API key audit:", report)


@pytest.mark.parametrize("key", REQUIRED_KEYS)
def test_required_key_present(key: str):
    env = load_env()
    value = (env.get(key) or os.environ.get(key) or "").strip()
    if os.environ.get("QUALITY_CHECK_STRICT") != "1":
        pytest.skip("Strict key check — set QUALITY_CHECK_STRICT=1 on promotion runner")
    assert value, f"{key} is missing"
    assert value.lower() not in {"changeme", "placeholder", "your-key-here", "xxx"}


@pytest.mark.skipif(
    os.environ.get("QUALITY_CHECK_LIVE_CALLS") != "1",
    reason="Set QUALITY_CHECK_LIVE_CALLS=1 to hit live APIs",
)
def test_deepseek_api_reachable():
    env = load_env()
    api_key = (env.get("DEEPSEEK_API_KEY") or "").strip()
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
    env = load_env()
    api_key = (env.get("ANTHROPIC_API_KEY") or env.get("CLAUDE_API_KEY") or "").strip()
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
    env = load_env()
    api_key = (env.get("GEMINI_API_KEY") or "").strip()
    assert api_key
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
        json={
            "contents": [{"parts": [{"text": "ping"}]}],
            "generationConfig": {"maxOutputTokens": 1},
        },
        timeout=30,
    )
    assert response.status_code in (200, 402, 429), response.text[:200]
