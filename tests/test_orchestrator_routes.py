"""Orchestrator multi-model API — the mock stack is labelled, never "success"."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_founder_restore import _reset_db_path


@pytest.fixture()
def founder_client(tmp_path, monkeypatch):
    _reset_db_path(monkeypatch, tmp_path / "history.sqlite")
    monkeypatch.setattr(
        "prompt_matrix.routers.orchestrator_routes._provider_keys_configured",
        lambda: False,
    )
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    return create_app(require_auth=False).test_client()


def test_orchestrate_requires_intent(founder_client) -> None:
    res = founder_client.post("/api/projects/founder/orchestrate", json={})
    assert res.status_code == 400
    assert res.get_json()["error"] == "intent is required"


def test_orchestrate_returns_claude_and_deepseek(founder_client) -> None:
    res = founder_client.post(
        "/api/projects/founder/orchestrate",
        json={"intent": "Compare liability limits for Boston renewal."},
    )
    assert res.status_code == 200
    body = res.get_json()
    # No provider keys: the payload is the fixed mock stack and must say so.
    assert body["status"] == "mock"
    assert body["mock"] is True
    assert body["stack"] == "mock"
    assert "illustrative" in body["notice"].lower()
    assert body["intent"] == "Compare liability limits for Boston renewal."
    assert body["models"]["claude"]["name"] == "Claude 3.5 Sonnet"
    assert "$5,000,000" in body["models"]["claude"]["text"]
    assert body["models"]["deepseek"]["name"] == "DeepSeek V3"
    assert "$4,500,000" in body["models"]["deepseek"]["text"]
