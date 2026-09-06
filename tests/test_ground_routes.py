"""Hybrid surgical grounding routes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prompt_matrix.db.jdf_repository import ensure_project, save_jdf_revision
from prompt_matrix.models.jdf import empty_annotations


def _tree() -> dict:
    return {
        "document_id": "doc-g",
        "meta": {"title": "g"},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "s1",
                "title": "S",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "p1",
                        "content": "Kızılay was founded in 1868.",
                        "entities_referenced": [],
                        "provenance": [],
                        "meta": {},
                        "annotations": empty_annotations(),
                    }
                ],
                "meta": {},
                "annotations": empty_annotations(),
            }
        ],
    }


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "g.sqlite"))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    ensure_project("default")
    save_jdf_revision("default", _tree(), mutation_type="seed")
    return create_app(require_auth=False).test_client()


def _node_json(content: str) -> str:
    return json.dumps(
        {
            "type": "paragraph",
            "id": "p1",
            "content": content,
            "meta": {},
        }
    )


def test_ground_search_mode(tmp_path, monkeypatch) -> None:
    snippets = [
        {
            "source_url": "https://example.com/kizilay",
            "snippet": "The Turkish Red Crescent (Kızılay) was founded in 1868 in Istanbul.",
            "title": "Kızılay",
        }
    ]
    monkeypatch.setattr(
        "prompt_matrix.services.ground_node.search_brave_web",
        lambda *_a, **_k: snippets,
    )
    monkeypatch.setattr(
        "prompt_matrix.services.ground_node._complete",
        lambda model, messages: _node_json("Founded in 1868 per search."),
    )
    client = _client(tmp_path, monkeypatch)
    res = client.post("/api/projects/default/nodes/p1/ground", json={"mode": "search"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["method_used"] == "search"
    assert body["search_attribution"][0]["source_url"].startswith("https://")
    assert body["suggested"]["meta"]["search_attribution"][0]["snippet"]


def test_ground_llm_mode(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "prompt_matrix.services.ground_node.search_brave_web",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        "prompt_matrix.services.ground_node._complete",
        lambda model, messages: _node_json("Clarified with internal knowledge."),
    )
    client = _client(tmp_path, monkeypatch)
    res = client.post("/api/projects/default/nodes/p1/ground", json={"mode": "llm"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["method_used"] == "llm"
    assert "Clarified" in body["suggested"]["content"]


def test_ground_auto_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "prompt_matrix.services.ground_node.search_brave_web",
        lambda *_a, **_k: [{"source_url": "https://x.test", "snippet": "tiny", "title": "t"}],
    )

    def _complete(model, messages):
        assert "claude" in model or "gpt" in model
        return _node_json("Fell back to LLM.")

    monkeypatch.setattr("prompt_matrix.services.ground_node._complete", _complete)
    client = _client(tmp_path, monkeypatch)
    res = client.post("/api/projects/default/nodes/p1/ground", json={"mode": "auto"})
    assert res.status_code == 200
    assert res.get_json()["method_used"] == "llm"


def test_ground_node_not_found(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("prompt_matrix.services.ground_node.search_brave_web", lambda *_a, **_k: [])
    client = _client(tmp_path, monkeypatch)
    res = client.post("/api/projects/default/nodes/missing/ground", json={"mode": "llm"})
    assert res.status_code == 404


def test_ground_invalid_mode(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    res = client.post("/api/projects/default/nodes/p1/ground", json={"mode": "telepathy"})
    assert res.status_code == 400
