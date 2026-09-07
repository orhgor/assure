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


def test_create_run_unanchored_no_sources():
    with patch("prompt_matrix.services.run_creation.run_lock_inference") as infer:
        run = create_run_from_directive(
            "Investigate Q3 revenue narrative",
            workspace_id="ws-alpha",
            source_ids=[],
        )
    infer.assert_not_called()
    assert run["status"] == "draft"
    assert run["unanchored"] is True
    assert run["extracted_locks"] == []
    assert run["directive"] == "Investigate Q3 revenue narrative"
    assert run["workspace_id"] == "ws-alpha"
    assert run["content"]["body"]


def test_create_run_with_sources_stamped(monkeypatch):
    def fake_fetch(_ws, ids):
        return [
            {"id": ids[0], "filename": "brief.pdf", "extracted_text": "Revenue was $12M in 2024."}
        ]

    def fake_locks(_text):
        return (
            [
                {
                    "canonical_key": "Revenue",
                    "value": 12_000_000,
                    "metric": "Revenue",
                    "confidence": 0.9,
                }
            ],
            "deepseek/deepseek-chat",
        )

    def fake_verify(_locks, _text):
        return {"status": "PASS", "locks_verified": 1}

    monkeypatch.setattr(
        "prompt_matrix.services.run_creation.fetch_substrate_entries_by_ids",
        fake_fetch,
    )
    monkeypatch.setattr("prompt_matrix.services.run_creation.run_lock_inference", fake_locks)
    monkeypatch.setattr("prompt_matrix.services.run_creation.verify_locks", fake_verify)

    run = create_run_from_directive(
        "Summarize revenue from sources",
        workspace_id="default",
        source_ids=["file_abc"],
    )
    assert run["status"] == "stamped"
    assert run["unanchored"] is False
    assert len(run["extracted_locks"]) == 1
    assert run["sources_used"][0]["id"] == "file_abc"


def test_post_api_runs(wb_client, monkeypatch):
    monkeypatch.setattr(
        "prompt_matrix.routers.runs_routes.create_run_from_directive",
        lambda directive, **kw: {
            "id": "run_test123",
            "workspace_id": kw.get("workspace_id"),
            "directive": directive,
            "content": {},
            "model": kw.get("model", "gemini"),
            "sources_used": [],
            "extracted_locks": [],
            "status": "draft",
            "unanchored": True,
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
            "lock_count": 0,
            "title": directive[:72],
        },
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
