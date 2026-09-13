"""SQLite pool must return checkouts after count_sends_today (no holder leak)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture()
def pooled_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "history.sqlite"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("SQLITE_USE_POOL", "1")
    try:
        from prompt_matrix.db.pool import reset_engine_for_tests

        reset_engine_for_tests()
    except ImportError:
        from db.pool import reset_engine_for_tests

        reset_engine_for_tests()
    yield db_path
    try:
        from prompt_matrix.db.pool import reset_engine_for_tests

        reset_engine_for_tests()
    except ImportError:
        from db.pool import reset_engine_for_tests

        reset_engine_for_tests()


def test_count_sends_today_releases_pool_checkout(pooled_db: Path) -> None:
    from prompt_matrix.db.pool import _pool_holders
    from prompt_matrix.history import count_sends_today

    before = len(_pool_holders)
    for _ in range(25):
        assert count_sends_today() == 0
    after = len(_pool_holders)
    assert after == before, f"pool holder leak: {before} -> {after}"
