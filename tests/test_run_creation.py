"""Founder workbench Phase 1 — synchronous run creation."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from prompt_matrix.models.jdf import build_document_from_draft, document_to_dict
from prompt_matrix.services.run_creation import create_run_from_directive


@pytest.fixture()
def wb_client(tmp_path, monkeypatch):
    db_path = tmp_path / "founder.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    return create_app(require_auth=False).test_client()


def _mock_auto_run(**overrides):
    base = {
        "id": "run_test123",
        "workspace_id": "ws-alpha",
        "directive": "Investigate Q3 revenue narrative",
        "content": document_to_dict(
            build_document_from_draft("ws-alpha", "Investigate Q3 revenue narrative")
        ),
        "model": "gemini",
        "sources_used": [],
        "extracted_locks": [],
        "status": "draft",
        "unanchored": True,
        "created_at": "2026-01-01 00:00:00",
        "updated_at": "2026-01-01 00:00:00",
        "lock_count": 0,
        "title": "Investigate Q3 revenue narrative",
    }
    base.update(overrides)
    return base


def test_create_run_unanchored_no_sources():
    with patch("prompt_matrix.services.auto_compiler.run_auto_compiler_pipeline") as pipe:
        pipe.return_value = iter(
            [
                'event: complete\ndata: {"ok":true,"run":'
                + '{"id":"run_1","status":"draft","unanchored":true,'
                + '"extracted_locks":[],"directive":"Investigate Q3 revenue narrative",'
                + '"workspace_id":"ws-alpha","content":{"body":[]},"sources_used":[],'
                + '"model":"gemini","created_at":"t","updated_at":"t"}'
                + "}\n\n"
            ]
        )
        run = create_run_from_directive(
            "Investigate Q3 revenue narrative",
            workspace_id="ws-alpha",
            source_ids=[],
        )
    assert run["status"] == "draft"
    assert run["directive"] == "Investigate Q3 revenue narrative"


def test_create_run_with_sources_stamped(monkeypatch):
    stamped = _mock_auto_run(
        status="stamped",
        unanchored=False,
        sources_used=[{"id": "file_abc", "name": "brief.pdf"}],
        extracted_locks=[
            {
                "canonical_key": "Revenue",
                "value": 12_000_000,
                "metric": "Revenue",
                "confidence": 0.9,
            }
        ],
        lock_count=1,
    )

    monkeypatch.setattr(
        "prompt_matrix.services.auto_compiler.run_auto_compiler_pipeline",
        lambda *_a, **_k: iter(
            [f'event: complete\ndata: {{"ok":true,"run":{__import__("json").dumps(stamped)}}}\n\n']
        ),
    )

    run = create_run_from_directive(
        "Summarize revenue from sources",
        workspace_id="default",
        source_ids=["file_abc"],
    )
    assert run["status"] == "stamped"
    assert len(run["extracted_locks"]) == 1


def test_post_api_runs(wb_client, monkeypatch):
    monkeypatch.setattr(
        "prompt_matrix.routers.runs_routes.create_run_from_directive",
        lambda directive, **kw: _mock_auto_run(
            id="run_test123",
            directive=directive,
            workspace_id=kw.get("workspace_id"),
        ),
    )
    res = wb_client.post(
        "/api/runs",
        json={"directive": "Check runway assumptions", "workspace_id": "default"},
    )
    assert res.status_code == 201
    data = res.get_json()
    assert data["ok"] is True
    assert data["run"]["id"] == "run_test123"
    assert data["run"]["directive"] == "Check runway assumptions"


def test_post_api_runs_requires_directive(wb_client):
    res = wb_client.post("/api/runs", json={"directive": "  "})
    assert res.status_code == 400
    assert res.get_json()["ok"] is False
