"""Global user activity audit log tests."""

from __future__ import annotations

import pytest

from prompt_matrix.web import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "activity.db"))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    app = create_app(require_auth=False)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_api_actions_logged_and_retrievable(client):
    create = client.post("/api/projects", json={"title": "Audit Log Test"})
    assert create.status_code == 201
    pid = create.get_json()["id"]

    client.get(f"/api/projects/{pid}/jdf")

    res = client.get("/api/audit-log?user_id=anonymous")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["count"] >= 1
    actions = {e["action"] for e in body["entries"]}
    assert "GET" in actions or "POST" in actions


def test_audit_log_requires_user(client):
    res = client.get("/api/audit-log")
    assert res.status_code == 401
