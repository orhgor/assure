"""Integration-style tests for edge substrate ingest flow."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest


def _reset_db_path(monkeypatch, db_path) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()


@pytest.fixture()
def edge_client(tmp_path, monkeypatch):
    db_path = tmp_path / "history.sqlite"
    _reset_db_path(monkeypatch, db_path)
    monkeypatch.setenv("SUBSTRATE_INGEST_SECRET", "test-edge-secret")
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    app = create_app(require_auth=False)
    return app.test_client(), str(db_path)


def test_edge_worker_to_substrate_flow(edge_client):
    """Simulate Worker POST /api/substrate after edge PDF extraction."""
    client, db_path = edge_client
    extracted = (
        "Revenue grew 12% year over year. Net income reached $4.2M in Q3. "
        "Operating margin expanded to 18%."
    )
    res = client.post(
        "/api/substrate",
        json={
            "projectId": "proj-edge-flow",
            "text": extracted,
            "pageCount": 3,
            "filename": "cim-excerpt.pdf",
            "source": "edge-unpdf",
        },
        headers={"X-Assure-Worker-Secret": "test-edge-secret"},
    )
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["ok"] is True
    assert payload["text_chars"] == len(extracted)

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT project_id, raw_text, page_count, source FROM substrates WHERE project_id = ?",
        ("proj-edge-flow",),
    ).fetchone()
    vault = conn.execute(
        "SELECT extracted_text, page_count FROM substrate_vault WHERE project_id = ?",
        ("proj-edge-flow",),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[1] == extracted
    assert row[2] == 3
    assert row[3] == "edge-unpdf"
    assert vault is not None
    assert vault[0] == extracted


def test_edge_flow_rejects_missing_secret(edge_client):
    client, _db = edge_client
    res = client.post(
        "/api/substrate",
        json={
            "projectId": "proj-edge-deny",
            "text": "Enough characters here for a valid substrate ingest payload.",
        },
    )
    assert res.status_code == 401


@patch("prompt_matrix.routers.substrate.save_substrate_text", side_effect=RuntimeError("db down"))
def test_edge_flow_returns_500_on_db_failure(mock_save, edge_client):
    client, _db = edge_client
    res = client.post(
        "/api/substrate",
        json={
            "projectId": "proj-edge-fail",
            "text": "Enough characters here for a valid substrate ingest payload.",
        },
        headers={"X-Assure-Worker-Secret": "test-edge-secret"},
    )
    assert res.status_code == 500
    assert res.get_json()["ok"] is False
