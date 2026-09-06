"""Sign-off route tests."""

from __future__ import annotations

import pytest

from prompt_matrix.web import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "signoff.db"))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    app = create_app(require_auth=False)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_sign_off_create_and_list(client):
    create = client.post("/api/projects", json={"title": "Sign-off Test"})
    pid = create.get_json()["id"]

    res = client.post(
        f"/api/projects/{pid}/sign-off",
        json={"status": "approved", "reviewer_name": "Alice", "comment": "LGTM"},
    )
    assert res.status_code == 201
    body = res.get_json()
    assert body["ok"] is True
    assert body["sign_off"]["status"] == "approved"

    listed = client.get(f"/api/projects/{pid}/sign-offs")
    assert listed.status_code == 200
    items = listed.get_json()["sign_offs"]
    assert len(items) >= 1
    assert items[0]["reviewer_name"] == "Alice"


def test_node_sign_off(client):
    create = client.post("/api/projects", json={"title": "Node Sign-off"})
    pid = create.get_json()["id"]
    res = client.post(
        f"/api/projects/{pid}/nodes/p-test/sign-off",
        json={"status": "rejected", "comment": "Needs revision"},
    )
    assert res.status_code == 201
    assert res.get_json()["sign_off"]["node_id"] == "p-test"


def test_sign_off_survives_jdf_round_trip(client):
    create = client.post("/api/projects", json={"title": "Round Trip"})
    pid = create.get_json()["id"]
    client.post(
        f"/api/projects/{pid}/sign-off",
        json={"status": "approved", "reviewer_name": "Bob"},
    )
    client.put(
        f"/api/projects/{pid}/jdf",
        json={
            "document": {
                "document_id": f"doc-{pid}",
                "meta": {"title": "Updated"},
                "truth_ledger": {},
                "body": [
                    {
                        "type": "paragraph",
                        "id": "p1",
                        "content": "Hello",
                        "meta": {},
                        "annotations": {"redhat": [], "z3": []},
                    }
                ],
            }
        },
    )
    listed = client.get(f"/api/projects/{pid}/sign-offs")
    assert len(listed.get_json()["sign_offs"]) >= 1
