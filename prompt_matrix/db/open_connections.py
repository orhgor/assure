"""Connections this process has taken from SQLite and not given back.

The connection-leakage family — three instances measured, one shape: **a failed or
interrupted write leaves a resource that damages the NEXT writer.**
`lib/logger.py`'s audit-drop path held its failed insert's connection open and the
next write timed out; `db/pipeline_cache.py`'s refused write left its statement
uncommitted so SQLite kept the write lock and the next write was silently lost
(fixed there by rolling the statement back before re-raising). Both are the same
defect: a connection that is neither committed, nor rolled back, nor closed — so
it holds an fd *and* whatever SQLite lock its open statement took.

`_apply_pragmas` (history.py) is where every connection's pragmas are applied, and
`_new_connection`/`_release_connection` plus `db/pool.py` are the only places a
connection is handed out or taken back. Counting the difference between those two
events is therefore a real count of connections this process is holding — the
quantity the family damages, and the one no other counter on /api/health reports.

A number, not a flag. It is a gauge, so it falls as well as rises: a connection
returned to the pool (or closed) leaves the registry, and a leaked one stays in it
for the life of the process. Read on /api/health as `db_open_connections`, the
same shape as `audit_drops` and `cache_drops` (`lib/logger.py`): module-level
state, one lock, one accessor.

Keyed by `id(conn)` rather than by holding the connection: a `sqlite3.Connection`
is not weak-referenceable (measured, CPython 3.13), and a strong reference here
would keep a leaked connection — and its fd — alive, turning the detector into the
defect. The registry therefore counts leases, not objects: what was taken and not
returned. Only our own release paths write to it, so a connection that is never
handed back through them stays counted even after its eventual GC closes the fd —
which is the state worth seeing, not the state worth hiding.
"""

from __future__ import annotations

import threading

# id(conn) -> the site that took it. The site is kept so a leak found through the
# count can be named without a second instrumented run.
_open_connections: dict[int, str] = {}
_open_connections_lock = threading.Lock()


def note_connection_open(conn, *, site: str) -> None:
    """Record a connection handed to a caller. Mirrors note_cache_drop's shape."""
    with _open_connections_lock:
        _open_connections[id(conn)] = site


def note_connection_released(conn) -> None:
    """Record a connection committed, rolled back and closed (or pooled back)."""
    with _open_connections_lock:
        _open_connections.pop(id(conn), None)


def db_open_connection_count() -> int:
    """Connections taken and not returned. Reported by /api/health."""
    with _open_connections_lock:
        return len(_open_connections)


def db_open_connection_sites() -> dict[str, int]:
    """How many of the open connections each site is holding, most first."""
    with _open_connections_lock:
        counts: dict[str, int] = {}
        for site in _open_connections.values():
            counts[site] = counts.get(site, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
