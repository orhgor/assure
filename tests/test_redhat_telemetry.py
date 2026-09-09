"""Red-Hat telemetry API and repository tests."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from prompt_matrix.db.connection import init_db
from prompt_matrix.db.drafts_repository import upsert_draft
from prompt_matrix.db.redhat_telemetry_repository import (
    fetch_telemetry,
    reset_telemetry,
    upsert_telemetry,
)
from prompt_matrix.history import get_db


def _reset_db_path(monkeypatch, db_path):
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()


@pytest.fixture
def telemetry_db(tmp_path, monkeypatch):
    _reset_db_path(monkeypatch, tmp_path / "redhat_telemetry.sqlite")
    init_db()
    return tmp_path


@pytest.fixture()
def founder_client(tmp_path, monkeypatch):
    _reset_db_path(monkeypatch, tmp_path / "history.sqlite")
    init_db()
    from prompt_matrix.web import create_app

    app = create_app(require_auth=False)
    return app.test_client()


def test_telemetry_roundtrip(telemetry_db):
    reset_telemetry("founder", task_id="task-1")
    upsert_telemetry(
        "founder",
        status="pass1_complete",
        pass1_complete=True,
        pass2_running=False,
        findings=[
            {
                "id": "rh_test",
                "pass": 1,
                "title": "Gap",
                "content": "Missing cite",
                "severity": "medium",
                "cache_hit": True,
                "block_hash": "abc",
                "patch_html": '<span class="diff-add">fix</span>',
            }
        ],
    )
    tel = fetch_telemetry("founder")
    assert tel is not None
    assert tel["pass1_complete"] is True
    assert tel["status"] == "pass1_complete"
    assert tel["findings"][0]["title"] == "Gap"
    assert tel["findings"][0]["cache_hit"] is True


def test_redhat_auto_and_status_routes(telemetry_db, founder_client):
    doc = {
        "document_id": "doc-1",
        "meta": {"project_id": "founder"},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "sec-1",
                "title": "Terms",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "p-1",
                        "content": "Liability capped at $1M.",
                        "meta": {},
                        "annotations": {"redhat": [], "z3": []},
                    }
                ],
                "meta": {},
                "annotations": {"redhat": [], "z3": []},
            }
        ],
    }
    upsert_draft(workspace_id="founder", content=doc)

    with patch(
        "prompt_matrix.routers.redhat_routes.schedule_redhat_multipass",
        return_value="celery-task-1",
    ):
        resp = founder_client.post("/api/projects/founder/redhat/auto", json={})
        assert resp.status_code == 202
        payload = resp.get_json()
        assert payload["ok"] is True
        assert payload["status"]["pass1_complete"] is False

        status_resp = founder_client.get("/api/projects/founder/redhat/status")
        assert status_resp.status_code == 200
        status_payload = status_resp.get_json()
        assert status_payload["ok"] is True
        assert status_payload["status"]["state"] == "pending"

        tel = fetch_telemetry("founder")
        assert tel is not None
        assert tel["task_id"] == "celery-task-1"
