"""Optimistic locking on JDF writes."""

from __future__ import annotations

from pathlib import Path

import pytest

from prompt_matrix.db.jdf_repository import (
    RevisionConflict,
    current_document_version,
    ensure_project,
    save_jdf_revision,
)
from prompt_matrix.models.jdf import empty_annotations


def _tree(text: str) -> dict:
    return {
        "document_id": "doc-cas",
        "meta": {"title": "cas"},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "s1",
                "title": "S",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "p1",
                        "content": text,
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
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "cas.sqlite"))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()


def test_concurrent_refine(tmp_path, monkeypatch) -> None:
    _reset(tmp_path, monkeypatch)
    ensure_project("cas")
    first = save_jdf_revision("cas", _tree("one"), mutation_type="seed")
    assert first["version"] == 1
    save_jdf_revision("cas", _tree("two"), mutation_type="refine", expected_version=1)
    with pytest.raises(RevisionConflict) as exc:
        save_jdf_revision("cas", _tree("three"), mutation_type="refine", expected_version=1)
    assert exc.value.latest_version == 2


def test_refine_after_manual_edit(tmp_path, monkeypatch) -> None:
    _reset(tmp_path, monkeypatch)
    from prompt_matrix.web import create_app

    ensure_project("cas2")
    save_jdf_revision("cas2", _tree("base"), mutation_type="seed")
    app = create_app(require_auth=False)
    client = app.test_client()
    manual = client.put(
        "/api/projects/cas2/jdf",
        json={"document": _tree("manual"), "expected_version": 1},
    )
    assert manual.status_code == 200
    stale = client.put(
        "/api/projects/cas2/jdf",
        json={"document": _tree("stale refine"), "expected_version": 1},
    )
    assert stale.status_code == 409
    body = stale.get_json()
    assert "Conflict" in body["error"]
    assert body["latest_version"] == current_document_version("cas2")
    assert "current_content" in body
