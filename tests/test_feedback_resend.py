"""Resend-backed user feedback endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from prompt_matrix.web import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "feedback.db"))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    from prompt_matrix.db.pool import reset_engine_for_tests

    reset_engine_for_tests()
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    app = create_app(require_auth=False)
    app.config["TESTING"] = True
    app.config["RESEND_API_KEY"] = ""
    with app.test_client() as c:
        yield c


def test_feedback_requires_message(client):
    res = client.post("/api/feedback", json={})
    assert res.status_code == 400


def test_feedback_stores_without_resend(client):
    client.application.config["RESEND_API_KEY"] = ""
    res = client.post(
        "/api/feedback",
        json={"message": "Great workbench UX", "url": "https://staging.getassureai.com/app"},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "ok"
    assert body["id"].startswith("fb-")


def test_feedback_sends_email_when_resend_configured(client):
    client.application.config["RESEND_API_KEY"] = "re_test_key"
    client.application.config["FEEDBACK_SEND_EMAIL"] = "1"
    with patch("resend.Emails.send") as mock_send:
        res = client.post("/api/feedback", json={"message": "Ship it"})
    assert res.status_code == 200
    mock_send.assert_called_once()


def test_tester_feedback_alias(client):
    client.application.config["RESEND_API_KEY"] = ""
    res = client.post("/api/tester-feedback", json={"text": "Legacy alias still works"})
    assert res.status_code == 200


def test_compose_feedback_still_accepts_run_hash(client, monkeypatch):
    monkeypatch.setattr(
        "prompt_matrix.template_library.apply_feedback",
        lambda run_hash, rating, vid: {"ok": True, "already": False},
    )
    res = client.post(
        "/api/feedback",
        json={"run_hash": "abc123", "rating": 1, "variation_id": 2},
    )
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"
