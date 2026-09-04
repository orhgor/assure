"""POST /api/tester-feedback — free-text notes for beta testers."""

from __future__ import annotations

import sqlite3
import uuid

import pytest


@pytest.fixture()
def feedback_client(tmp_path, monkeypatch):
    db_path = tmp_path / "history.sqlite"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    try:
        from prompt_matrix.lib import logger as logger_mod

        logger_mod._audit_singleton = None
    except ImportError:
        pass
    from prompt_matrix.db.connection import init_db

    conn = sqlite3.connect(str(db_path))
    init_db(conn)
    conn.close()

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
    assert res.get_json().get("status") == "ok"

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        """
        SELECT action, success, details FROM audit_log
        WHERE action='TESTER_FEEDBACK' ORDER BY created_at DESC LIMIT 1
        """
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "TESTER_FEEDBACK"
    assert row[1] == 1
    assert "Export button" in (row[2] or "")
