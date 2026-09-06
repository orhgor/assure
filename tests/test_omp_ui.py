"""OMP decision log API tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from prompt_matrix.web import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "omp.db"))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    app = create_app(require_auth=False)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_omp_memories_list_for_project_tag(client):
    fake = {
        "memories": [
            {
                "key": "threshold-change",
                "content": "Threshold changed because Red-Hat flagged it.",
                "created_at": "2026-09-08T12:00:00Z",
            }
        ]
    }
    with patch("prompt_matrix.routers.omp_routes.omp_list_memories", return_value=fake):
        res = client.get("/api/omp/memories?tags=project:prj_demo")
    assert res.status_code == 200
    body = res.get_json()
    memories = body.get("memories") or body
    assert len(memories) >= 1
    assert "Red-Hat" in memories[0]["content"]


def test_decision_log_ui_endpoint_shape(client):
    """Frontend expects memories array with key, content, timestamp."""
    payload = {
        "memories": [{"key": "k1", "content": "Decision A", "created_at": "2026-09-08T10:00:00Z"}]
    }
    with patch("prompt_matrix.routers.omp_routes.omp_list_memories", return_value=payload):
        res = client.get("/api/omp/memories?tags=project:test")
    data = res.get_json()
    mem = (data.get("memories") or data)[0]
    assert mem["key"] == "k1"
    assert "content" in mem
