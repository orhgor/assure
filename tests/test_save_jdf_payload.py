"""JDF PUT accepts frontend envelopes (title, type: document, extra node fields)."""

from __future__ import annotations

from pathlib import Path

import pytest


def _reset_db_path(monkeypatch, db_path: Path) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()


@pytest.fixture()
def jdf_client(tmp_path, monkeypatch):
    _reset_db_path(monkeypatch, tmp_path / "history.sqlite")
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    app = create_app(require_auth=False)
    return app.test_client()


def test_put_jdf_accepts_title_and_type_envelope(jdf_client) -> None:
    res = jdf_client.put(
        "/api/projects/codecheck/jdf",
        json={"document": {"type": "document"}, "title": "Q3 Update"},
    )
    assert res.status_code == 200, res.get_json()
    payload = res.get_json()
    assert payload.get("ok") is not False
    doc = payload.get("document") or {}
    assert doc.get("document_id") == "doc-codecheck"
    assert (doc.get("meta") or {}).get("title") == "Q3 Update"


def test_put_jdf_ignores_extra_node_fields(jdf_client) -> None:
    tree = {
        "document_id": "doc-extra",
        "title": "Root title",
        "type": "document",
        "meta": {},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "sec-1",
                "title": "Section",
                "confidence": 0.9,
                "children": [
                    {
                        "type": "paragraph",
                        "id": "p-1",
                        "content": "Hello.",
                        "gutter": "verified",
                        "entities_referenced": [],
                        "provenance": [],
                        "meta": {},
                        "annotations": {"redhat": [], "z3": []},
                    }
                ],
                "meta": {},
                "annotations": {"redhat": [], "z3": []},
            }
        ],
    }
    res = jdf_client.put("/api/projects/extra-fields/jdf", json={"document": tree})
    assert res.status_code == 200, res.get_json()
    doc = res.get_json().get("document") or {}
    assert (doc.get("meta") or {}).get("title") == "Root title"
    para = doc["body"][0]["children"][0]
    assert para["content"] == "Hello."
    assert "gutter" not in para
