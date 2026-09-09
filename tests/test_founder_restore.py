"""Founder workbench — project history restore API."""

from __future__ import annotations

from pathlib import Path

import pytest

SAMPLE_TREE = {
    "document_id": "doc-founder",
    "meta": {"project_id": "founder"},
    "truth_ledger": {},
    "body": [
        {
            "type": "section",
            "id": "sec-1",
            "title": "Draft",
            "children": [
                {
                    "type": "paragraph",
                    "id": "para-1",
                    "content": "Version one text.",
                    "meta": {},
                }
            ],
        }
    ],
}

SAMPLE_TREE_V2 = {
    **SAMPLE_TREE,
    "body": [
        {
            "type": "section",
            "id": "sec-1",
            "title": "Draft",
            "children": [
                {
                    "type": "paragraph",
                    "id": "para-1",
                    "content": "Version two text.",
                    "meta": {},
                }
            ],
        }
    ],
}


def _reset_db_path(monkeypatch, db_path: Path) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()


@pytest.fixture()
def founder_client(tmp_path, monkeypatch):
    _reset_db_path(monkeypatch, tmp_path / "history.sqlite")
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    app = create_app(require_auth=False)
    return app.test_client()


def test_history_lists_revisions(founder_client) -> None:
    res1 = founder_client.put(
        "/api/projects/founder/jdf",
        json={
            "document": SAMPLE_TREE,
            "mutation_type": "MANUAL_TOUCHUP",
            "change_summary": "First save",
        },
    )
    assert res1.status_code == 200, res1.get_json()
    res2 = founder_client.put(
        "/api/projects/founder/jdf",
        json={
            "document": SAMPLE_TREE_V2,
            "mutation_type": "GENERATE_DOCK",
            "change_summary": "Second save",
        },
    )
    assert res2.status_code == 200, res2.get_json()

    hist = founder_client.get("/api/projects/founder/history")
    assert hist.status_code == 200
    payload = hist.get_json()
    assert payload.get("ok") is True
    assert payload.get("count") == 2
    history = payload.get("history") or []
    assert len(history) == 2
    assert history[0]["version"] == 2
    assert history[0]["mutation_type"] == "GENERATE_DOCK"
    assert history[0]["change_summary"] == "Second save"
    assert history[0].get("timestamp")


def test_restore_updates_draft_and_returns_document(founder_client) -> None:
    founder_client.put(
        "/api/projects/founder/jdf",
        json={"document": SAMPLE_TREE, "mutation_type": "MANUAL_TOUCHUP"},
    )
    founder_client.put(
        "/api/projects/founder/jdf",
        json={"document": SAMPLE_TREE_V2, "mutation_type": "GENERATE_DOCK"},
    )

    restore = founder_client.post(
        "/api/projects/founder/restore",
        json={"version": 1, "workspace_id": "founder"},
    )
    assert restore.status_code == 200, restore.get_json()
    body = restore.get_json()
    assert body.get("ok") is True
    assert body.get("version") == 1
    doc = body.get("document") or {}
    assert doc["body"][0]["children"][0]["content"] == "Version one text."

    draft = founder_client.get("/api/drafts?workspace_id=founder")
    assert draft.status_code == 200
    draft_doc = (draft.get_json().get("draft") or {}).get("content") or {}
    assert draft_doc["body"][0]["children"][0]["content"] == "Version one text."


def test_restore_missing_version_returns_404(founder_client) -> None:
    res = founder_client.post("/api/projects/founder/restore", json={"version": 99})
    assert res.status_code == 404
