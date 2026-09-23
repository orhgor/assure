"""Connection pool facade over the PostgreSQL backend.

The historical SQLAlchemy QueuePool for a SQLite file is gone with the file.
``db/pg_compat`` owns the psycopg pool; this module keeps the names the rest
of the code and the tests use (``checkout_dbapi_connection``,
``release_dbapi_connection``, ``connection_is_pooled``, ``get_engine``,
``dispose_engine``) so callers did not have to change.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from .open_connections import note_connection_open, note_connection_released
from .pg_compat import checkout, close_all_pools, is_compat_connection, sqlalchemy_url

try:
    from ..history import _resolve_db_path
except ImportError:
    from history import _resolve_db_path

_log = logging.getLogger("assure")

_engine: Any | None = None
_engine_url: str | None = None
_engine_lock = threading.Lock()


def get_engine():
    """A SQLAlchemy engine on the same PostgreSQL DSN (Celery's db backend,
    ad-hoc reporting). Rebuilt when ``DATABASE_URL`` changes."""
    global _engine, _engine_url
    url = sqlalchemy_url()
    if url is None:
        raise RuntimeError("DATABASE_URL is not a PostgreSQL DSN")
    if _engine is not None and _engine_url == url:
        return _engine
    with _engine_lock:
        if _engine is not None and _engine_url == url:
            return _engine
        from sqlalchemy import create_engine

        _engine = create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=3)
        _engine_url = url
        return _engine


def checkout_dbapi_connection():
    """Borrow a connection from the shared PostgreSQL pool."""
    conn = checkout(str(_resolve_db_path()))
    note_connection_open(conn, site="db.pool.checkout_dbapi_connection")
    return conn


def release_dbapi_connection(conn) -> None:
    """Return a borrowed connection to the pool."""
    if conn is None:
        return
    try:
        conn.close()
    except Exception as exc:
        _log.error(
            "PostgreSQL connection not returned to the pool (%s: %s); "
            "db_open_connections still counts it as open",
            exc.__class__.__name__,
            exc,
        )
        return
    note_connection_released(conn)


def connection_is_pooled(conn) -> bool:
    return is_compat_connection(conn)


def dispose_engine() -> None:
    """Drop every pooled connection (tests call this between databases)."""
    global _engine, _engine_url
    with _engine_lock:
        if _engine is not None:
            try:
                _engine.dispose()
            except Exception:
                pass
        _engine = None
        _engine_url = None
    close_all_pools()


def reset_engine_for_tests() -> None:
    """Alias kept for existing fixtures."""
    dispose_engine()
