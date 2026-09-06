"""POST /api/tester-feedback — free-text notes for beta testers."""

from __future__ import annotations

import json
import sqlite3

import pytest


@pytest.fixture()
def feedback_client(tmp_path, monkeypatch):
    db_path = tmp_path / "history.sqlite"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    try:
        import prompt_matrix.lib.logger as logger_mod

        logger_mod.DB_PATH = history_mod.DB_PATH
        logger_mod._audit_singleton = None
    except ImportError:
        pass
    from prompt_matrix.db.connection import init_db

    init_db()

    from prompt_matrix.web import create_app

    app = create_app(require_auth=False)
    return app.test_client(), str(db_path)


def test_tester_feedback_requires_text(feedback_client):
    client, _db = feedback_client
    res = client.post("/api/tester-feedback", json={"text": "   "})
    assert res.status_code == 400


def test_tester_feedback_logs_audit_row(feedback_client):
    client, db_path = feedback_client
    res = client.post(
        "/api/tester-feedback",
        json={"text": "Export button was unclear", "page": "/app"},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body.get("status") == "ok"
    assert body.get("id", "").startswith("fb-")

    conn = sqlite3.connect(db_path)
    audit = conn.execute(
        """
        SELECT action, success, details FROM audit_log
        WHERE action='USER_FEEDBACK' ORDER BY created_at DESC LIMIT 1
        """
    ).fetchone()
    row = conn.execute("SELECT message FROM feedback ORDER BY created_at DESC LIMIT 1").fetchone()
    conn.close()
    assert audit is not None
    assert audit[0] == "USER_FEEDBACK"
    assert audit[1] == 1
    details = json.loads(audit[2] or "{}")
    assert "Export button" in details.get("message_preview", "")
    assert row is not None
    assert "Export button" in row[0]
