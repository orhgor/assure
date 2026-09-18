"""Pipeline cache TTL."""

from __future__ import annotations

from pathlib import Path

import pytest

from prompt_matrix.db.pipeline_cache import fetch_pipeline_cache, save_pipeline_cache
from prompt_matrix.history import get_db
from prompt_matrix.services.omp_memory import load_ast_cache, save_ast_cache


def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "cache.sqlite"))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    # pipeline_cache.project_id declares the FK to projects, so the project a
    # cache row names has to exist before the row can be written.
    from prompt_matrix.db.jdf_repository import ensure_project

    ensure_project("p1")


def test_cache_ttl_set(tmp_path, monkeypatch) -> None:
    _reset(tmp_path, monkeypatch)
    save_pipeline_cache("ast:t:1", "p1", "ast", {"compiled": {"node_count": 1}})
    db = get_db()
    row = db.execute(
        "SELECT expires_at FROM pipeline_cache WHERE cache_key = ?",
        ("ast:t:1",),
    ).fetchone()
    assert row is not None
    assert row[0]


def test_cache_expires(tmp_path, monkeypatch) -> None:
    _reset(tmp_path, monkeypatch)
    save_ast_cache("ast:t:exp", "p1", {"compiled": {"node_count": 2, "lock_count": 0}})
    db = get_db()
    db.execute(
        "UPDATE pipeline_cache SET expires_at = datetime('now', '-1 day') WHERE cache_key = ?",
        ("ast:t:exp",),
    )
    db.commit()
    assert fetch_pipeline_cache("ast:t:exp") is None
    assert load_ast_cache("ast:t:exp") is None
