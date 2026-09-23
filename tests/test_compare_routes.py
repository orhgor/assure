"""POST /api/runs/compare — parallel model dispatch."""

from __future__ import annotations

import pytest

from tests.test_founder_restore import _reset_db_path


@pytest.fixture()
def client(tmp_path, monkeypatch):
    _reset_db_path(monkeypatch, tmp_path / "history.sqlite")
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    return create_app(require_auth=False).test_client()


def test_compare_requires_intent(client) -> None:
    res = client.post("/api/runs/compare", json={})
    assert res.status_code == 400
    assert res.get_json()["error"] == "intent is required"


def test_compare_returns_two_models(client, monkeypatch) -> None:
    async def _fake_async(intent, source_ids=None, pair_index=0):
        return {
            "model_a": {
                "model": "gemini/gemini-2.5-flash",
                "name": "Gemini 2.5 Flash",
                "text": "Alpha output",
                "jdf": {"text": "Alpha output", "divergences": []},
            },
            "model_b": {
                "model": "openrouter/qwen/qwen3-next-80b-a3b-instruct",
                "name": "DeepSeek V3",
                "text": "Beta output",
                "jdf": {"text": "Beta output", "divergences": []},
            },
            "models": {
                "claude": {"name": "Gemini 2.5 Flash", "text": "Alpha output"},
                "kimi": {"name": "DeepSeek V3", "text": "Beta output"},
            },
        }

    monkeypatch.setenv("ASSURE_USE_FREE_MODELS", "1")
    monkeypatch.setattr(
        "prompt_matrix.routers.compare_routes.run_compare_pair_async",
        _fake_async,
    )

    res = client.post(
        "/api/runs/compare",
        json={"intent": "Summarise risks", "source_ids": ["doc-1"]},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["stack"] == "free"
    assert body["model_a"]["text"] == "Alpha output"
    assert body["model_b"]["text"] == "Beta output"
