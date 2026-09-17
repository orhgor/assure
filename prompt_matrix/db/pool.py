"""Shared SQLite connection pool (SQLAlchemy QueuePool)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool

try:
    from ..history import _apply_pragmas, _resolve_db_path
except ImportError:
    from history import _apply_pragmas, _resolve_db_path

_engine: Engine | None = None
_engine_db_path: str | None = None
_engine_lock = threading.Lock()
_pool_holders: dict[int, Any] = {}
_pool_holders_lock = threading.Lock()

DEFAULT_POOL_SIZE = int(__import__("os").environ.get("SQLITE_POOL_SIZE", "5"))
DEFAULT_MAX_OVERFLOW = int(__import__("os").environ.get("SQLITE_POOL_MAX_OVERFLOW", "15"))
# A saturated pool used to block every DB route for SQLAlchemy's default 30s.
DEFAULT_POOL_TIMEOUT = float(__import__("os").environ.get("SQLITE_POOL_TIMEOUT", "5"))


def _sqlite_url(db_path: Path) -> str:
    return f"sqlite+pysqlite:///{db_path.resolve()}"


def get_engine() -> Engine:
    """Return the QueuePool engine, rebuilt when DATABASE_PATH changes.

    The engine is cached per-process, but compared on every call against
    the currently resolved DB path. If the path diverges (e.g. a test
    rebinds history.DB_PATH or the runtime override changes), a fresh engine
    is constructed for the new path; the old engine is left to GC so any
    in-flight borrowed connections remain valid until released.
    """
    global _engine, _engine_db_path
    current = str(_resolve_db_path())
    if _engine is not None and _engine_db_path == current:
        return _engine
    with _engine_lock:
        if _engine is not None and _engine_db_path == current:
            return _engine
        db_path = Path(current)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        eng = create_engine(
            _sqlite_url(db_path),
            poolclass=QueuePool,
            pool_size=DEFAULT_POOL_SIZE,
            max_overflow=DEFAULT_MAX_OVERFLOW,
            pool_timeout=DEFAULT_POOL_TIMEOUT,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False, "timeout": 30.0},
        )

        @event.listens_for(eng, "connect")
        def _on_connect(dbapi_conn: sqlite3.Connection, _record: Any) -> None:
            dbapi_conn.row_factory = sqlite3.Row
            _apply_pragmas(dbapi_conn)

        _engine = eng
        _engine_db_path = current
        return _engine


def checkout_dbapi_connection() -> sqlite3.Connection:
    """Borrow a sqlite3 connection from the shared pool."""
    raw = get_engine().raw_connection()
    conn = raw.driver_connection
    if conn is None:
        raise RuntimeError("pool returned connection without driver_connection")
    conn.row_factory = sqlite3.Row
    with _pool_holders_lock:
        _pool_holders[id(conn)] = raw
    return conn


def release_dbapi_connection(conn: sqlite3.Connection | None) -> None:
    """Return a borrowed connection to the pool."""
    if conn is None:
        return
    with _pool_holders_lock:
        raw = _pool_holders.pop(id(conn), None)
    if raw is not None:
        raw.close()
        return
    try:
        conn.close()
    except Exception:
        pass


def connection_is_pooled(conn: sqlite3.Connection | None) -> bool:
    if conn is None:
        return False
    with _pool_holders_lock:
        return id(conn) in _pool_holders


def reset_engine_for_tests() -> None:
    """Dispose pool between tests (call from fixtures when DATABASE_PATH changes)."""
    global _engine, _engine_db_path
    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
            _engine = None
        _engine_db_path = None
