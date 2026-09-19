"""The connection-leakage family: a failed write must not damage the NEXT writer.

Three instances were measured before this file existed, one shape: a failed or
interrupted write leaves a resource that damages the next writer.
`lib/logger.py`'s audit-drop path held its failed insert's connection open, so the
next audit write timed out; `db/pipeline_cache.py`'s refused write left its
statement uncommitted, so SQLite kept the write lock and the next cache write was
silently lost (fixed at that cause, with its own test); and the `/health` probes,
the metrics collector and the compliance export each opened a connection and were
guaranteed to close it only when nothing raised.

The tests below split into two kinds, and the split is the point:

*Detectors* — they fail against the pre-fix base, because they measure what the
next writer gets, with no reference to the fix itself. They import nothing this
change added, so the pre-fix run reaches the behaviour rather than an ImportError.

    test_a_refused_audit_row_does_not_block_the_next_audit_write
    test_a_failed_pool_checkout_returns_the_connection_to_the_pool
    test_a_failed_migration_leaves_no_open_transaction

*Guards* — they hold contracts the fix must keep, and the new counter's contract.
They cannot fail pre-fix (the counter they read did not exist), which is stated
rather than presented as coverage:

    test_an_audit_write_leaves_no_connection_behind
    test_a_failed_direct_write_leaves_the_database_writable
    test_a_borrowed_connection_is_counted_while_it_is_held
    test_a_deliberate_load_holds_many_connections_and_gives_all_of_them_back
    test_pooled_writes_give_every_connection_back
    test_api_health_reports_the_open_connection_count
"""

from __future__ import annotations

import sqlite3
import threading
import time
import types
from pathlib import Path

import pytest

from prompt_matrix.db import connection as connection_mod
from prompt_matrix.db import pool
from prompt_matrix.lib.logger import AuditLogger


def _migrated_audit_db(path: Path) -> None:
    """The shape staging runs: audit_log.project_id references projects(id).

    scripts/aws/migrate_fk_constraints.py rebuilds audit_log that way, so a write
    for a project that does not exist is refused by the table rather than stored —
    which is what makes the drop path reachable at all.
    """
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, title TEXT NOT NULL)")
    conn.execute(
        """
        CREATE TABLE audit_log (
            id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            project_id TEXT,
            action TEXT NOT NULL,
            target_node_id TEXT,
            success INTEGER NOT NULL,
            duration_ms INTEGER,
            error_type TEXT,
            error_message TEXT,
            details TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute("INSERT INTO projects (id, title) VALUES ('proj-1', 'proj-1')")
    conn.commit()
    conn.close()


def _audit_rows(path: Path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return [row[0] for row in conn.execute("SELECT request_id FROM audit_log ORDER BY rowid")]
    finally:
        conn.close()


# --- detectors ----------------------------------------------------------------


def test_a_refused_audit_row_does_not_block_the_next_audit_write(tmp_path: Path) -> None:
    """A refused audit row must cost its own row and nothing else.

    Pre-fix the refused insert's connection was never closed, so it kept its
    transaction open and SQLite kept the write lock; the next audit write — from a
    connection of its own — waited out busy_timeout and failed with `database is
    locked`, and that failure is swallowed the same way. One dropped row turned
    into a run of them.
    """
    db_path = tmp_path / "history.sqlite"
    _migrated_audit_db(db_path)
    audit = AuditLogger(str(db_path))

    audit.log_audit("req-refused", "no-such-project", "DRAFT_STREAM", success=True)
    audit.log_audit("req-next", "proj-1", "DRAFT_STREAM", success=True)

    # The refused row is absent and the next one is present: the drop is reported
    # by the audit-drop counter and the log line, not by the next write failing.
    assert _audit_rows(db_path) == ["req-next"]


def test_a_failed_pool_checkout_returns_the_connection_to_the_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A checkout that cannot produce a connection gives the pool's fairy back.

    The fairy is already out of the pool when the check below fails; raising
    without returning it shrinks the pool by one for the life of the process, and
    the next writer that needs that slot waits out pool_timeout and fails — the
    family's shape, with the pool's capacity as the damaged resource.
    """

    class _Fairy:
        driver_connection = None
        returned = False

        def close(self) -> None:
            self.returned = True

    fairy = _Fairy()
    monkeypatch.setattr(
        pool, "get_engine", lambda: types.SimpleNamespace(raw_connection=lambda: fairy)
    )

    with pytest.raises(RuntimeError):
        pool.checkout_dbapi_connection()

    assert fairy.returned is True


def test_a_failed_migration_leaves_no_open_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A migration that fails half way must not leave its work pending.

    Callers reach init_db() through get_db(), so the connection is the request's
    own. An uncommitted statement holds SQLite's write lock for as long as the
    request lives, and whoever commits that connection next — the request's own
    write — commits the partial migration with it. The failing migration below does
    a DML statement before it raises on purpose: DDL alone runs in autocommit and
    would leave nothing to roll back, which is a test that could not fail.
    """
    db_path = tmp_path / "history.sqlite"
    conn = sqlite3.connect(str(db_path))

    def _migration_that_writes_then_fails(db: sqlite3.Connection) -> None:
        db.execute("INSERT INTO schema_migrations (version) VALUES (?)", (9001,))
        raise sqlite3.OperationalError("migration failed")

    monkeypatch.setattr(connection_mod, "_migrate_v5", _migration_that_writes_then_fails)
    with pytest.raises(sqlite3.OperationalError):
        connection_mod.init_db(conn)

    try:
        assert conn.in_transaction is False
    finally:
        conn.close()


# --- guards -------------------------------------------------------------------


def test_an_audit_write_leaves_no_connection_behind(tmp_path: Path) -> None:
    """Both audit paths — the row that lands and the row that is refused."""
    from prompt_matrix.db.open_connections import db_open_connection_count

    db_path = tmp_path / "history.sqlite"
    _migrated_audit_db(db_path)
    audit = AuditLogger(str(db_path))
    baseline = db_open_connection_count()

    audit.log_audit("req-landed", "proj-1", "DRAFT_STREAM", success=True)
    assert db_open_connection_count() == baseline

    audit.log_audit("req-refused", "no-such-project", "DRAFT_STREAM", success=True)
    assert db_open_connection_count() == baseline


def test_a_failed_direct_write_leaves_the_database_writable(tmp_path: Path) -> None:
    """closing_connection's contract, which every direct call site now rests on.

    A body that writes and then raises leaves nothing of its statement behind, and
    the proof is the next writer's success rather than an internal flag.
    """
    from prompt_matrix.db.connection import closing_connection

    db_path = tmp_path / "history.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError):
        with closing_connection(db_path) as conn:
            conn.execute("INSERT INTO notes (body) VALUES ('uncommitted')")
            raise RuntimeError("the write that failed")

    other = sqlite3.connect(str(db_path), timeout=1.0)
    try:
        other.execute("INSERT INTO notes (body) VALUES ('next')")
        other.commit()
        assert [row[0] for row in other.execute("SELECT body FROM notes")] == ["next"]
    finally:
        other.close()


def test_a_borrowed_connection_is_counted_while_it_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gauge is a gauge: it rises with a connection in hand and falls after."""
    from prompt_matrix.db.open_connections import db_open_connection_count
    from prompt_matrix.history import borrowed_connection

    import prompt_matrix.history as history_mod

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "history.sqlite"))
    history_mod.DB_PATH = history_mod._resolve_db_path()
    baseline = db_open_connection_count()

    with borrowed_connection() as conn:
        conn.execute("SELECT 1")
        assert db_open_connection_count() == baseline + 1

    assert db_open_connection_count() == baseline


def test_a_deliberate_load_holds_many_connections_and_gives_all_of_them_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Four writers holding a connection at once is what the count reports.

    The barrier's action runs once every writer has arrived, so the number is read
    while all four connections are genuinely in hand — not sampled and hoped for.
    """
    from prompt_matrix.db.open_connections import db_open_connection_count
    from prompt_matrix.history import borrowed_connection

    import prompt_matrix.history as history_mod

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "history.sqlite"))
    history_mod.DB_PATH = history_mod._resolve_db_path()
    baseline = db_open_connection_count()
    writers, writes_each = 4, 25
    observed: list[int] = []
    barrier = threading.Barrier(
        writers, action=lambda: observed.append(db_open_connection_count() - baseline)
    )
    errors: list[BaseException] = []

    def _writer() -> None:
        try:
            for index in range(writes_each):
                for attempt in range(20):
                    try:
                        with borrowed_connection() as conn:
                            conn.execute(
                                "CREATE TABLE IF NOT EXISTS writes "
                                "(id INTEGER PRIMARY KEY, n INT)"
                            )
                            conn.execute("INSERT INTO writes (n) VALUES (?)", (index,))
                            conn.commit()
                            if index == 0:
                                # Every writer is inside its own `with` when the last
                                # one arrives, so the barrier's action reads the count
                                # with four connections genuinely in hand.
                                barrier.wait()
                        break
                    except sqlite3.OperationalError:
                        # Four writers on one file: `database is locked` is the
                        # contention this test deliberately creates, not the defect
                        # under test, so it retries instead of failing the run.
                        if attempt == 19:
                            raise
                        time.sleep(0.05 * (attempt + 1))
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=_writer) for _ in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert observed == [writers]
    assert db_open_connection_count() == baseline


def test_pooled_writes_give_every_connection_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pooled path counts too, and a request-scoped write returns its checkout."""
    from prompt_matrix.db.open_connections import db_open_connection_count
    from prompt_matrix.history import count_sends_today

    import prompt_matrix.history as history_mod

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "history.sqlite"))
    monkeypatch.setenv("SQLITE_USE_POOL", "1")
    history_mod.DB_PATH = history_mod._resolve_db_path()
    pool.reset_engine_for_tests()
    try:
        baseline = db_open_connection_count()
        for _ in range(25):
            count_sends_today()
        assert db_open_connection_count() == baseline
    finally:
        pool.reset_engine_for_tests()


def test_api_health_reports_the_open_connection_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/api/health carries the number, the way it carries audit_drops and cache_drops.

    Read at rest and with a connection in hand: a health key that cannot move would
    not be worth emitting.
    """
    import prompt_matrix.history as history_mod
    from prompt_matrix.history import borrowed_connection
    from prompt_matrix.web import create_app

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "history.sqlite"))
    history_mod.DB_PATH = history_mod._resolve_db_path()
    client = create_app(require_auth=False).test_client()

    # A baseline rather than an absolute zero: app boot itself holds a few
    # connections (named by db_open_connection_sites()), and this asserts what the
    # endpoint is for — the number moves with the connections, and back.
    baseline = client.get("/api/health").get_json()["db_open_connections"]
    assert isinstance(baseline, int)

    with borrowed_connection() as conn:
        conn.execute("SELECT 1")
        under_load = client.get("/api/health").get_json()
        assert under_load["db_open_connections"] == baseline + 1

    assert client.get("/api/health").get_json()["db_open_connections"] == baseline
