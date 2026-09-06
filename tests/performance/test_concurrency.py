"""Performance tests: concurrent SQLite access under gevent-style load."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from prompt_matrix.db.connection import init_db, open_connection, run_with_db_retry
from prompt_matrix.db.pool import (
    checkout_dbapi_connection,
    release_dbapi_connection,
    reset_engine_for_tests,
)


@pytest.fixture
def pooled_db(tmp_path, monkeypatch):
    db_path = tmp_path / "concurrency.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("SQLITE_POOL_SIZE", "8")
    monkeypatch.setenv("SQLITE_POOL_MAX_OVERFLOW", "8")
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    reset_engine_for_tests()
    init_db()
    yield db_path
    reset_engine_for_tests()


def _write_project(conn: sqlite3.Connection, idx: int) -> None:
    pid = f"perf-{idx}"
    run_with_db_retry(
        conn.execute,
        "INSERT OR REPLACE INTO projects (id, title, current_version) VALUES (?, ?, 1)",
        (pid, f"Concurrent {idx}"),
    )
    run_with_db_retry(conn.commit)


def test_fifty_concurrent_pool_writes_no_database_locked(pooled_db):
    """50 parallel writers via QueuePool must not raise 'database is locked'."""
    errors: list[str] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        conn = checkout_dbapi_connection()
        try:
            _write_project(conn, i)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower():
                with lock:
                    errors.append(str(exc))
            else:
                raise
        finally:
            release_dbapi_connection(conn)

    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = [pool.submit(worker, i) for i in range(50)]
        for fut in as_completed(futures):
            fut.result()

    assert errors == [], f"database locked errors: {errors[:5]}"
    verify = open_connection()
    try:
        count = verify.execute("SELECT COUNT(*) FROM projects WHERE id LIKE 'perf-%'").fetchone()[0]
    finally:
        verify.close()
    assert count == 50


def test_wal_mode_enabled(pooled_db):
    conn = open_connection()
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()
    assert str(mode).lower() == "wal"
    assert int(timeout) >= 5000
