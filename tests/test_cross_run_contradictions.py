"""Founder workbench Phase 1 — cross-run contradiction detection."""

from __future__ import annotations

import pytest

from prompt_matrix.db.runs_repository import insert_run
from prompt_matrix.models.jdf import build_document_from_draft, document_to_dict
from prompt_matrix.services.macro_verify import detect_cross_run_contradictions


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


def _run_doc(text: str) -> dict:
    doc = build_document_from_draft("default", text)
    return document_to_dict(doc)


def test_detect_numeric_contradiction_across_runs(tmp_path, monkeypatch):
    db_path = tmp_path / "founder.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()

    run_a = insert_run(
        directive="Run A",
        content=_run_doc("Revenue reached $10M in 2024."),
        model="gemini",
        sources_used=[],
        extracted_locks=[{"canonical_key": "Revenue", "value": 10_000_000, "metric": "Revenue"}],
        status="stamped",
        workspace_id="default",
    )
    run_b = insert_run(
        directive="Run B",
        content=_run_doc("Revenue reached $15M in 2024."),
        model="gemini",
        sources_used=[],
        extracted_locks=[{"canonical_key": "Revenue", "value": 15_000_000, "metric": "Revenue"}],
        status="contradiction",
        workspace_id="default",
    )

    conflicts = detect_cross_run_contradictions([run_a["id"], run_b["id"]])
    assert conflicts
    assert any(c["conflict_type"] == "numeric" for c in conflicts)
    assert conflicts[0]["severity"] == "high"


def test_contradictions_endpoint(wb_client):
    run_a = insert_run(
        directive="Alpha",
        content=_run_doc("ARR is $5M."),
        model="gemini",
        sources_used=[],
        extracted_locks=[{"canonical_key": "ARR", "value": 5_000_000, "metric": "ARR"}],
        status="stamped",
        workspace_id="default",
    )
    run_b = insert_run(
        directive="Beta",
        content=_run_doc("ARR is $8M."),
        model="gemini",
        sources_used=[],
        extracted_locks=[{"canonical_key": "ARR", "value": 8_000_000, "metric": "ARR"}],
        status="stamped",
        workspace_id="default",
    )

    res = wb_client.get(f"/api/runs/{run_a['id']}/contradictions")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["count"] >= 1
    assert any(c["conflict_type"] == "numeric" for c in data["conflicts"])

    res2 = wb_client.get(
        f"/api/runs/{run_a['id']}/contradictions",
        query_string={"run_ids": run_b["id"]},
    )
    assert res2.status_code == 200
    assert res2.get_json()["count"] >= 1


def test_single_run_returns_no_conflicts(wb_client):
    run = insert_run(
        directive="Solo",
        content=_run_doc("Only one claim."),
        model="gemini",
        sources_used=[],
        extracted_locks=[],
        status="draft",
        workspace_id="default",
    )
    res = wb_client.get(f"/api/runs/{run['id']}/contradictions")
    assert res.status_code == 200
    assert res.get_json()["count"] == 0
