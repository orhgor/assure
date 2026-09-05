"""Flask error handlers — JSON for /api/* and friendly HTML elsewhere."""

from __future__ import annotations

import pytest


@pytest.fixture()
def web_client(monkeypatch):
    monkeypatch.delenv("PLAUSIBLE_ENABLED", raising=False)
    monkeypatch.delenv("SENTRY_ENABLED", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    from prompt_matrix.web import create_app

    return create_app(require_auth=False).test_client()


def test_api_404_returns_json(web_client):
    res = web_client.get("/api/foo")
    assert res.status_code == 404
    assert res.is_json
    payload = res.get_json()
    assert "error" in payload
    assert payload["error"]


def test_html_404_returns_friendly_page(monkeypatch):
    monkeypatch.setenv("ASSURE_REQUIRE_LOGIN", "false")
    from prompt_matrix.web import create_app

    client = create_app(require_auth=False).test_client()
    res = client.get("/this-page-does-not-exist")
    assert res.status_code == 404
    html = res.get_data(as_text=True)
    assert "error-page" in html or "Page not found" in html or "couldn't find" in html.lower()


def test_help_url_default_mailto(web_client):
    res = web_client.get("/about")
    html = res.get_data(as_text=True)
    assert 'href="mailto:feedback@getassureai.com"' in html


def test_help_url_env_override(web_client, monkeypatch):
    monkeypatch.setenv("ASSURE_HELP_URL", "https://discord.gg/example")
    from prompt_matrix.web import create_app

    client = create_app(require_auth=False).test_client()
    res = client.get("/about")
    html = res.get_data(as_text=True)
    assert 'href="https://discord.gg/example"' in html
