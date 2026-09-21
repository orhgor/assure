"""Lock evidence API and resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    from prompt_matrix.db.runs_repository import insert_run
    from prompt_matrix.db.substrate_repository import save_substrate_entry
    from prompt_matrix.services.lock_metadata import enrich_extracted_locks
except ImportError:
    from db.runs_repository import insert_run
    from db.substrate_repository import save_substrate_entry
    from services.lock_metadata import enrich_extracted_locks


def _reset_db_path(monkeypatch, db_path: Path) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()


@pytest.fixture()
def founder_client(tmp_path, monkeypatch):
    _reset_db_path(monkeypatch, tmp_path / "history.sqlite")
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    app = create_app(require_auth=False)
    return app.test_client()


def test_lock_evidence_endpoint(founder_client) -> None:
    save_substrate_entry(
        "founder",
        filename="NAIC_Underwriting_Policy_2025.pdf",
        page_count=1,
        extracted_text="Revenue reached $12M in Q3 per underwriting policy.",
        entry_id="sub-pw-1",
    )
    locks = enrich_extracted_locks(
        [{"canonical_key": "Revenue", "value": 12_000_000, "metric": "Revenue"}],
        [{"id": "sub-pw-1", "name": "NAIC_Underwriting_Policy_2025.pdf"}],
    )
    lock_hash = locks[0]["lock_hash"]
    insert_run(
        directive="Revenue narrative",
        content={"body": [], "truth_ledger": {"Revenue": 12_000_000}},
        model="gemini",
        sources_used=[{"id": "sub-pw-1", "name": "NAIC_Underwriting_Policy_2025.pdf"}],
        extracted_locks=locks,
        status="stamped",
        workspace_id="founder",
        run_id="run_evidence01",
    )

    res = founder_client.get(f"/api/locks/{lock_hash}/evidence")
    assert res.status_code == 200, res.get_json()
    payload = res.get_json()
    assert payload.get("ok") is True
    assert payload.get("source_name") == "NAIC_Underwriting_Policy_2025.pdf"
    assert payload.get("page_number") == 1
    assert "Revenue" in (payload.get("excerpt") or "")
    assert "z3_proof" in payload


def test_lock_evidence_not_found(founder_client) -> None:
    res = founder_client.get("/api/locks/missinghash000000/evidence")
    assert res.status_code == 404
