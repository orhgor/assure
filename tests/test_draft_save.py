"""Founder workbench Phase 1 — draft persistence."""

from __future__ import annotations

import pytest

from prompt_matrix.models.jdf import build_document_from_draft, document_to_dict


@pytest.fixture()
def wb_client(tmp_path, monkeypatch):
    db_path = tmp_path / "founder.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    return create_app(require_auth=False).test_client()


def _sample_draft() -> dict:
    doc = build_document_from_draft("default", "Founder draft paragraph one.")
    return document_to_dict(doc)


def test_put_draft_creates_and_updates(wb_client):
    content = _sample_draft()
    res = wb_client.put(
        "/api/drafts",
        json={"workspace_id": "ws-draft", "content": content},
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["draft"]["workspace_id"] == "ws-draft"
    assert data["draft"]["content"]["body"]

    updated = _sample_draft()
    updated["body"][0]["children"][0]["content"] = "Updated paragraph."
    res2 = wb_client.put(
        "/api/drafts",
        json={"workspace_id": "ws-draft", "content": updated},
    )
    assert res2.status_code == 200
    assert res2.get_json()["draft"]["id"] == data["draft"]["id"]

    get_res = wb_client.get("/api/drafts?workspace_id=ws-draft")
    assert get_res.status_code == 200
    fetched = get_res.get_json()["draft"]
    assert fetched["content"]["body"][0]["children"][0]["content"] == "Updated paragraph."


def test_get_draft_missing_returns_null(wb_client):
    res = wb_client.get("/api/drafts?workspace_id=no-such-workspace")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["draft"] is None


def test_put_draft_requires_workspace(wb_client):
    res = wb_client.put("/api/drafts", json={"workspace_id": "  ", "content": {}})
    assert res.status_code == 400
