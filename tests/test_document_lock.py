"""Document lock tests."""

from __future__ import annotations

import pytest

from prompt_matrix.db.document_lock_repository import hash_jdf_tree
from prompt_matrix.web import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "lock.db"))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    app = create_app(require_auth=False)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _sample_doc(pid: str) -> dict:
    return {
        "document_id": f"doc-{pid}",
        "meta": {"title": "Lock test"},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "sec1",
                "title": "Body",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "p1",
                        "content": "Locked content",
                        "meta": {},
                        "annotations": {"redhat": [], "z3": []},
                    }
                ],
                "meta": {},
                "annotations": {"redhat": [], "z3": []},
            }
        ],
    }


def test_lock_blocks_jdf_edit(client):
    create = client.post("/api/projects", json={"title": "Lock Test"})
    pid = create.get_json()["id"]
    client.put(f"/api/projects/{pid}/jdf", json={"document": _sample_doc(pid)})

    lock = client.post(f"/api/projects/{pid}/lock")
    assert lock.status_code == 200
    lock_body = lock.get_json()
    assert lock_body["ok"] is True
    assert lock_body["lock"]["content_hash"]

    blocked = client.put(
        f"/api/projects/{pid}/jdf",
        json={"document": {**_sample_doc(pid), "meta": {"title": "Changed"}}},
    )
    assert blocked.status_code == 409
    assert blocked.get_json()["error"] == "Document is locked"


def test_lock_hash_consistency(client):
    doc = _sample_doc("x")
    h1 = hash_jdf_tree(doc)
    h2 = hash_jdf_tree(doc)
    assert h1 == h2
    doc2 = {**doc, "meta": {"title": "Other"}}
    assert hash_jdf_tree(doc2) != h1


def test_get_lock_status(client):
    create = client.post("/api/projects", json={"title": "Lock Status"})
    pid = create.get_json()["id"]
    res = client.get(f"/api/projects/{pid}/lock")
    assert res.status_code == 200
    assert res.get_json()["locked"] is False

    client.post(f"/api/projects/{pid}/lock")
    res2 = client.get(f"/api/projects/{pid}/lock")
    assert res2.get_json()["locked"] is True
