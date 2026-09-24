"""Keyword-level source conflict detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from prompt_matrix.db.jdf_repository import ensure_project, save_jdf_revision
from prompt_matrix.db.substrate_repository import save_substrate_entry
from prompt_matrix.ledger.source_conflicts import detect_source_conflicts
from prompt_matrix.models.jdf import empty_annotations


def _tree() -> dict:
    return {
        "document_id": "doc-cf",
        "meta": {"title": "cf"},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "s1",
                "title": "S",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "p-retain",
                        "content": "The retention period is 7 years.",
                        "entities_referenced": [],
                        "provenance": [],
                        "meta": {},
                        "annotations": empty_annotations(),
                    }
                ],
                "meta": {},
                "annotations": empty_annotations(),
            }
        ],
    }


def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "cf.sqlite"))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    ensure_project("cf")
    save_jdf_revision("cf", _tree(), mutation_type="seed")
    save_substrate_entry(
        "cf",
        filename="policy-a.pdf",
        page_count=1,
        extracted_text="Retention period is 7 years for all records.",
    )
    save_substrate_entry(
        "cf",
        filename="policy-b.pdf",
        page_count=1,
        extracted_text="Retention period is 5 years for all records.",
    )


def test_conflict_detection(tmp_path, monkeypatch) -> None:
    _reset(tmp_path, monkeypatch)
    rows = detect_source_conflicts("cf", _tree())
    assert rows
    assert any(
        "7 years" in r["conflict_description"] and "5 years" in r["conflict_description"]
        for r in rows
    )
    assert (
        "Naive" in rows[0]["conflict_description"]
        or "keyword" in rows[0]["conflict_description"].lower()
    )


def test_conflict_api_returns_list(tmp_path, monkeypatch) -> None:
    _reset(tmp_path, monkeypatch)
    from prompt_matrix.web import create_app

    client = create_app(require_auth=False).test_client()
    res = client.get("/api/projects/cf/conflicts")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["implementation"] == "keyword-level"
    assert body["count"] >= 1
    assert body["conflicts"]
