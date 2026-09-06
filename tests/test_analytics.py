"""Analytics SQL view endpoints."""

from __future__ import annotations

import pytest

from prompt_matrix.web import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "analytics.db"))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    app = create_app(require_auth=False)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_analytics_z3_health(client):
    res = client.get("/api/analytics/z3-health")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert "rows" in body


def test_analytics_redhat_critiques(client):
    res = client.get("/api/analytics/redhat-critiques")
    assert res.status_code == 200
    assert res.get_json()["ok"] is True


def test_analytics_compliance_velocity(client):
    res = client.get("/api/analytics/compliance-velocity")
    assert res.status_code == 200
    rows = res.get_json()["rows"]
    assert isinstance(rows, list)
