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


def _version_rows(project_id: str) -> list[int]:
    from prompt_matrix.history import get_db

    return [
        int(row[0])
        for row in get_db()
        .execute("SELECT version FROM jdf_revisions WHERE project_id = ? ORDER BY version", (project_id,))
        .fetchall()
    ]


def test_two_concurrent_saves_both_persist_and_neither_raises(tmp_path, monkeypatch) -> None:
    """The invariant: two compiles landing at once both persist, neither raises.

    ``version`` must be assigned *inside* the write lock. ``SELECT MAX(version)``
    read outside it meant two callers whose persists landed in the same instant
    both read the same maximum and both tried to insert it: one then raised
    ``UNIQUE constraint failed: jdf_revisions.project_id, jdf_revisions.version``,
    and the ``database is locked`` retry around the closure re-ran the INSERT
    against the still-open transaction and wrote a second row anyway. Measured by
    the fix's author with the window forced open and eight callers: seven
    ``database is locked`` and one UNIQUE, three rows for two versions.

    The window is forced open: a second connection holds ``BEGIN IMMEDIATE`` for
    as long as both callers are inside ``save_jdf_revision``, so no write can
    commit. ``ensure_project`` is stubbed to a no-op — the project exists, and its
    own write would otherwise be the thing that blocks, serializing the callers
    *before* the version read and closing the very window this test opens.

    A plain pair of threads does not discriminate: they queue behind
    ``ensure_project``'s write and each reads a fresh maximum in turn, which is why
    this test passes on the fixed code and did not fail on the reverted one until
    the stub was added.
    """
    import sqlite3
    import threading
    import time

    _reset(tmp_path, monkeypatch)
    ensure_project("cas-race")
    monkeypatch.setattr(
        "prompt_matrix.db.jdf_repository.ensure_project", lambda *_a, **_k: None
    )

    holder = sqlite3.connect(str(tmp_path / "cas.sqlite"), timeout=10, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")

    results: list[dict] = []
    errors: list[BaseException] = []
    start = threading.Barrier(2)

    def save(body: str) -> None:
        start.wait(timeout=10)
        try:
            results.append(
                save_jdf_revision("cas-race", _tree(body), mutation_type="compile")
            )
        except BaseException as exc:  # noqa: BLE001 - the assertion is that nothing raises
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(f"body {n}",)) for n in range(2)]
    for thread in threads:
        thread.start()
    # Let both callers reach the version read while no write can commit.
    time.sleep(0.5)
    holder.execute("ROLLBACK")
    holder.close()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == [], errors
    assert sorted(res["version"] for res in results) == [1, 2]
    assert _version_rows("cas-race") == [1, 2]


def test_a_stale_expected_version_still_conflicts_under_concurrency(tmp_path, monkeypatch) -> None:
    """The optimistic lock keeps its teeth: a second writer on a stale version is told.

    ``RevisionConflict`` carries the version that actually exists so the caller can
    rebase; that payload is read through a *new* connection (a PRAGMA cannot run
    inside the transaction the failed attempt opened), so it has to be the
    committed value and not the one the caller expected.
    """
    _reset(tmp_path, monkeypatch)
    ensure_project("cas-stale")
    save_jdf_revision("cas-stale", _tree("one"), mutation_type="seed")
    with pytest.raises(RevisionConflict) as exc:
        save_jdf_revision("cas-stale", _tree("two"), mutation_type="compile", expected_version=0)
    assert exc.value.latest_version == 1
    assert exc.value.current_content, "the conflict payload must carry the current document"
    assert _version_rows("cas-stale") == [1], "the refused attempt must not leave a row"
