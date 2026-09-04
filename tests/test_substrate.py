from __future__ import annotations

import sqlite3

import pytest


def _reset_db_path(monkeypatch, db_path) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()


@pytest.fixture()
def substrate_client(tmp_path, monkeypatch):
    db_path = tmp_path / "history.sqlite"
    _reset_db_path(monkeypatch, db_path)
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    app = create_app(require_auth=False)
    return app.test_client(), str(db_path)


def test_substrate_ingestion_requires_data(substrate_client):
    client, _db = substrate_client
    res = client.post("/api/substrate", json={})
    assert res.status_code == 400


def test_substrate_ingestion_saves_text(substrate_client):
    client, db_path = substrate_client
    res = client.post(
        "/api/substrate",
        json={
            "projectId": "proj-test-123",
            "text": "Simulated PDF text extracted from Cloudflare Worker via unpdf.",
        },
    )
    assert res.status_code == 200
    assert res.get_json().get("ok") is True

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        """
        SELECT project_id, raw_text FROM substrates
        WHERE project_id = 'proj-test-123'
        """
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "proj-test-123"
    assert "Simulated PDF text" in row[1]


def test_substrate_ingestion_rejects_short_text(substrate_client):
    client, _db = substrate_client
    res = client.post(
        "/api/substrate",
        json={"projectId": "proj-short", "text": "too short"},
    )
    assert res.status_code == 400


def test_substrate_ingestion_requires_secret_when_configured(substrate_client, monkeypatch):
    client, _db = substrate_client
    monkeypatch.setenv("SUBSTRATE_INGEST_SECRET", "edge-secret")
    res = client.post(
        "/api/substrate",
        json={
            "projectId": "proj-secure",
            "text": "Protected edge ingestion payload with enough characters.",
        },
    )
    assert res.status_code == 401

    res_ok = client.post(
        "/api/substrate",
        json={
            "projectId": "proj-secure",
            "text": "Protected edge ingestion payload with enough characters.",
        },
        headers={"X-Assure-Worker-Secret": "edge-secret"},
    )
    assert res_ok.status_code == 200
    assert res_ok.get_json().get("ok") is True
