"""Plausible and Sentry browser snippets — prod gating."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "prompt_matrix" / "templates"


@pytest.fixture()
def web_client(monkeypatch):
    monkeypatch.delenv("PLAUSIBLE_ENABLED", raising=False)
    monkeypatch.delenv("SENTRY_ENABLED", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("SENTRY_BROWSER_DSN", raising=False)
    from prompt_matrix.web import create_app

    return create_app(require_auth=False).test_client()


def test_observability_snippets_off_by_default(web_client):
    res = web_client.get("/")
    html = res.get_data(as_text=True)
    assert "plausible.io/js/pa-we0rKAtBU" not in html
    assert "sentry.bundle.js" not in html
    assert "window.__SENTRY_DSN" not in html


def test_plausible_enabled_override(web_client, monkeypatch):
    monkeypatch.setenv("PLAUSIBLE_ENABLED", "1")
    res = web_client.get("/")
    html = res.get_data(as_text=True)
    assert "plausible.io/js/pa-we0rKAtBU" in html


def test_sentry_browser_enabled_override(web_client, monkeypatch):
    monkeypatch.setenv("SENTRY_ENABLED", "1")
    monkeypatch.setenv("SENTRY_BROWSER_DSN", "https://example@sentry.io/1")
    res = web_client.get("/")
    html = res.get_data(as_text=True)
    assert "sentry.bundle.js" in html
    assert 'window.__SENTRY_DSN="https://example@sentry.io/1"' in html


def test_production_enables_both_snippets(web_client, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    res = web_client.get("/")
    html = res.get_data(as_text=True)
    assert "plausible.io/js/pa-we0rKAtBU" in html
    assert "sentry.bundle.js" in html
    assert "window.__SENTRY_DSN" in html


def test_sentry_browser_dsn_prefers_browser_env(web_client, monkeypatch):
    monkeypatch.setenv("SENTRY_ENABLED", "1")
    monkeypatch.setenv("SENTRY_DSN", "https://server@sentry.io/1")
    monkeypatch.setenv("SENTRY_BROWSER_DSN", "https://browser@sentry.io/1")
    res = web_client.get("/")
    html = res.get_data(as_text=True)
    assert 'window.__SENTRY_DSN="https://browser@sentry.io/1"' in html


def test_template_includes_exist():
    for name in ("plausible.html", "sentry.html"):
        path = TEMPLATES / "includes" / name
        assert path.is_file(), f"missing includes/{name}"
    for name in ("base.html", "landing.html", "architecture.html"):
        html = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "includes/plausible.html" in html
        assert "includes/sentry.html" in html


def test_plausible_and_sentry_helpers():
    from prompt_matrix.web import _plausible_enabled, _sentry_browser_dsn, _sentry_enabled

    saved = {
        k: os.environ.get(k)
        for k in (
            "PLAUSIBLE_ENABLED",
            "SENTRY_ENABLED",
            "ENVIRONMENT",
            "SENTRY_DSN",
            "SENTRY_BROWSER_DSN",
        )
    }
    try:
        for key in (
            "PLAUSIBLE_ENABLED",
            "SENTRY_ENABLED",
            "ENVIRONMENT",
            "SENTRY_DSN",
            "SENTRY_BROWSER_DSN",
        ):
            os.environ.pop(key, None)
        assert _plausible_enabled() is False
        assert _sentry_enabled() is False
        assert _sentry_browser_dsn() == ""

        os.environ["PLAUSIBLE_ENABLED"] = "1"
        assert _plausible_enabled() is True
        os.environ.pop("PLAUSIBLE_ENABLED", None)

        os.environ["SENTRY_ENABLED"] = "1"
        assert _sentry_enabled() is True
        os.environ.pop("SENTRY_ENABLED", None)

        os.environ["ENVIRONMENT"] = "production"
        assert _plausible_enabled() is True
        assert _sentry_enabled() is True
        assert _sentry_browser_dsn().endswith("/4512026963869696")

        os.environ["SENTRY_DSN"] = "https://server@sentry.io/1"
        assert _sentry_browser_dsn() == "https://server@sentry.io/1"

        os.environ["SENTRY_BROWSER_DSN"] = "https://browser@sentry.io/1"
        assert _sentry_browser_dsn() == "https://browser@sentry.io/1"
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
