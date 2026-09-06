"""Drift-check endpoint tests."""

from __future__ import annotations

import pytest

from prompt_matrix.web import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "drift.db"))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    app = create_app(require_auth=False)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _create_project(client):
    res = client.post("/api/projects", json={"title": "Drift Test"})
    assert res.status_code == 201
    return res.get_json()["id"]


def test_drift_check_pass_with_policy_text(client):
    pid = _create_project(client)
    res = client.post(
        f"/api/projects/{pid}/drift-check",
        json={
            "config": {"deductible_pct": 2, "liability_limit": 2000000},
            "policy_text": "deductible_pct: 2\nliability_limit: 2000000",
        },
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["status"] in ("PASS", "UNKNOWN")
    assert body["drift_detected"] is False


def test_drift_check_fail_on_mismatch(client):
    pid = _create_project(client)
    res = client.post(
        f"/api/projects/{pid}/drift-check",
        json={
            "config": {"deductible_pct": 5},
            "policy_text": "deductible_pct: 2",
        },
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "FAIL"
    assert body["drift_detected"] is True
    assert len(body["findings"]) >= 1


def test_drift_check_missing_policy(client):
    pid = _create_project(client)
    res = client.post(
        f"/api/projects/{pid}/drift-check",
        json={"config": {"a": 1}},
    )
    assert res.status_code == 400


def test_drift_check_missing_policy_id(client):
    pid = _create_project(client)
    res = client.post(
        f"/api/projects/{pid}/drift-check",
        json={"config": {"a": 1}, "policy_id": "missing-file"},
    )
    assert res.status_code == 404
