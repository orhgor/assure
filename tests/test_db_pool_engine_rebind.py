"""Regression test: db/pool engine re-binds when DATABASE_PATH changes.

Covers the bug where ``prompt_matrix.db.pool.get_engine()`` returned a
stale singleton bound to whatever DB path was active on the FIRST call,
so tests (and runtime) that re-pointed ``DATABASE_PATH`` /
``history_mod.DB_PATH`` would still operate against the old file. The
concrete downstream failure was
``sqlite3.OperationalError: duplicate column name: file_size_bytes``
when migrations were re-applied to an already-migrated DB from an
earlier test through the leaked engine.

The fix makes ``get_engine()`` compare ``_resolve_db_path()`` on every
call and rebuild the engine when it diverges. This test directly drives
``get_engine()`` through a path-change cycle (pool code path, which is
where the bug lived) and additionally drives ``init_db()`` through
``founder_client``-style resets to ensure the public surface stays
correct regardless of whether ``SQLITE_USE_POOL`` is on or off.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.test_founder_restore import _reset_db_path


def test_pool_engine_rebinds_after_db_path_change(tmp_path, monkeypatch) -> None:
    from prompt_matrix.db import pool

    # Always start with a clean singleton so prior tests do not leak in.
    pool.reset_engine_for_tests()
    assert pool._engine is None
    assert pool._engine_db_path is None

    # --- First DB path ---
    db1 = tmp_path / "db1.sqlite"
    _reset_db_path(monkeypatch, db1)

    from prompt_matrix.db.connection import init_db

    init_db()

    # Directly exercise pool.get_engine() so we cover the rebind logic
    # regardless of whether SQLITE_USE_POOL selects the non-pooled fallback
    # inside history._new_connection for the regular DB calls above.
    eng1 = pool.get_engine()
    assert eng1 is pool._engine
    assert pool._engine_db_path == str(db1.resolve())

    # --- Second DB path: must rebind the engine ---
    db2 = tmp_path / "db2.sqlite"
    _reset_db_path(monkeypatch, db2)

    # Sanity: calling the reset helper is NOT the thing that fixes it —
    # we want the auto-rebind inside get_engine() itself. So do NOT call
    # reset_engine_for_tests() between the two paths.
    init_db()

    eng2 = pool.get_engine()
    assert eng2 is not eng1, "pool engine should rebuild for new DB path, not reuse singleton"
    assert pool._engine is eng2
    assert pool._engine_db_path == str(db2.resolve()), (
        f"_engine_db_path {pool._engine_db_path!r} does not match new "
        f"DB path {str(db2.resolve())!r}"
    )
    assert db2.exists(), (
        "After second _reset_db_path + init_db, the new sqlite file must exist "
        "on disk. If it is missing, init_db() was still targeting db1 via a "
        "leaked engine/connection."
    )

    # Clean up so subsequent tests don't inherit our singleton.
    pool.reset_engine_for_tests()
    assert pool._engine is None
    assert pool._engine_db_path is None
