"""Rotating file logger and SQLite audit trail (fail-safe)."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:
    from ..history import DB_PATH, _apply_pragmas, _release_direct_connection
    from ..paths import user_data_dir
except ImportError:
    from history import DB_PATH, _apply_pragmas, _release_direct_connection
    from paths import user_data_dir

_log = logging.getLogger(__name__)

_audit_singleton: AuditLogger | None = None

# Audit rows that never landed. The insert is best-effort by design — log_audit
# swallows its own failure so that an audit write can never break the request it
# describes — which makes a failed insert otherwise invisible: the row is gone
# and only a log line says so. This count is what /api/health reports, so the
# trail losing entries is visible without grepping the service log.
#
# Process-wide rather than per-AuditLogger: get_audit_logger() rebuilds the
# singleton when DATABASE_PATH changes, and a row dropped by the old logger was
# dropped either way.
_audit_drops = 0
_audit_drops_lock = threading.Lock()


def audit_drop_count() -> int:
    """Audit rows dropped in this process. Reported by /api/health."""
    with _audit_drops_lock:
        return _audit_drops


def _record_audit_drop() -> None:
    global _audit_drops
    with _audit_drops_lock:
        _audit_drops += 1


# Pipeline-cache rows a write was refused. `pipeline_cache.project_id` references
# `projects(id)` on every database that has run
# scripts/aws/migrate_fk_constraints.py — the shape staging runs — so a write whose
# id names no project is rejected. The rejection is otherwise invisible: the row is
# simply absent, so the next read is a miss and reads as a cold start rather than as
# a write that never landed. Measured on the pre-migration staging database
# (2026-09-18): 24 pipeline_cache rows carried a project_id with no `projects` row.
#
# Same shape as the audit-row counter (`_audit_drops`, which is this idiom's
# precedent on 2eb27fc's ancestor f674370, "a dropped audit row is counted, and no
# longer blocks the next write"). Process-wide, because the write is not per-logger.
# Only the foreign-key rejection is counted, and only it may be swallowed: any other
# failure is a bug in the cache rather than a write with no parent, and it propagates
# to a caller that already has a policy for it.
_cache_drops = 0
_cache_drops_lock = threading.Lock()


def cache_drop_count() -> int:
    """Cache rows dropped in this process. Reported by /api/health."""
    with _cache_drops_lock:
        return _cache_drops


def note_cache_drop(*, site: str, project_id: str, kind: str) -> None:
    """Count one refused cache write, and name it in the service log."""
    global _cache_drops
    with _cache_drops_lock:
        _cache_drops += 1
    _log.warning(
        "[cache-drop] %s refused project_id=%r (kind=%s): no `projects` row owns it, "
        "so the row was not written and the next read is a miss",
        site,
        project_id,
        kind,
    )


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            try:
                from flask import g, has_app_context

                if has_app_context() and getattr(g, "request_id", None):
                    record.request_id = g.request_id
                    return True
            except Exception:
                pass
            record.request_id = "-"
        return True


def set_request_id() -> None:
    """Attach a request id for logs and Sentry."""
    try:
        from flask import g, request
    except ImportError:
        return
    g.request_id = (request.headers.get("X-Request-Id") or str(uuid.uuid4())).strip()
    try:
        import sentry_sdk

        sentry_sdk.set_tag("request_id", g.request_id)
    except Exception:
        pass


def _log_dir() -> Path:
    override = (os.environ.get("ASSURE_LOG_DIR") or "").strip()
    if override:
        path = Path(override)
    else:
        prod = Path("/home/ubuntu/assure/logs")
        path = prod if prod.parent.is_dir() else user_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_assure_logger() -> logging.Logger:
    """Application file logger with rotation."""
    log = logging.getLogger("assure")
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        _log_dir() / "assure.log",
        maxBytes=10_000_000,
        backupCount=5,
    )
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s [%(request_id)s] %(message)s")
    )
    handler.addFilter(_RequestIdFilter())
    log.addHandler(handler)
    return log


def resolve_db_path() -> str:
    override = (os.environ.get("DATABASE_PATH") or "").strip()
    if override:
        return override
    return str(DB_PATH)


class AuditLogger:
    """Writes structured audit rows to SQLite. Never raises to callers."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or resolve_db_path()
        self._file = get_assure_logger()

    def log_audit(
        self,
        request_id: str,
        project_id: str | None,
        action: str,
        *,
        target_node_id: str | None = None,
        success: bool = True,
        duration_ms: int | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        details: dict | None = None,
    ) -> None:
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            # The audit trail's connection is the fifth path to this database and
            # the only one that did not apply the shared pragmas, so its
            # `foreign_keys` defaulted to SQLite's OFF like every other
            # connection's would. It is applied here for parity with
            # history.get_db, db/pool.py and db/connection.py.
            #
            # On the repo's own DDL this changes nothing for an audit write
            # unless the database carries the constraint: a database created
            # before db/connection.py declared the FK for its seven unconstrained
            # tables has audit_log with no FOREIGN KEY on project_id, and no
            # pragma can reach a row there. A database that has run
            # scripts/aws/migrate_fk_constraints.py carries audit_log REBUILT
            # with `FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE
            # CASCADE` — measured on the staging box on 2026-09-18 — and a
            # database created from the DDL now carries the same clause, so on
            # both of those this pragma makes an audit write for an unknown
            # project FAIL.
            #
            # And a failed write is what the except below historically answered
            # with a log line and a return: it becomes an invisible missing
            # entry, which is worse than the orphan row it replaces. The guard
            # that keeps a missing project out of the trail is therefore the
            # route-level check in routers/retrieval_routes.py, and it has to run
            # BEFORE this write. Enforcement here is the second line, not the
            # first — and because it can now fail a row on a database that
            # declares the FK, a dropped row is counted as well as logged, so
            # /api/health can show the trail losing entries.
            _apply_pragmas(conn)
            conn.execute(
                """
                INSERT INTO audit_log
                (id, request_id, project_id, action, target_node_id, success,
                 duration_ms, error_type, error_message, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    request_id,
                    project_id,
                    action,
                    target_node_id,
                    1 if success else 0,
                    duration_ms,
                    error_type,
                    error_message,
                    json.dumps(details) if details else None,
                ),
            )
            conn.commit()
        except Exception as exc:
            # The row is gone and nothing else will miss it. Counting it is what
            # makes the loss visible outside this process (/api/health reads it),
            # and the log line below is what makes it visible in the service log
            # at the time it happened: an audit trail with a silently missing
            # entry is worse than one with a visible orphan, and this is the
            # failure mode the pragma above can now trigger.
            _record_audit_drop()
            _log.error(
                "AUDIT ROW DROPPED — %s (project=%s action=%s request=%s)",
                exc,
                project_id,
                action,
                request_id,
            )
            self._file.error(
                f"Audit log failed (SQLite): {exc}",
                extra={"request_id": request_id},
            )
            if error_message:
                self._file.error(
                    f"Original action: {action}, error: {error_message}",
                    extra={"request_id": request_id},
                )
        finally:
            # Released on both paths. A connection abandoned by a failed insert
            # keeps that insert's transaction open, so the next audit write — from
            # a connection of its own — waits on SQLite's write lock and times out:
            # one dropped row turning into a run of them. Rolled back rather than
            # relying on the close to discard the transaction, closed quietly
            # because this runs in a `finally` on a path whose whole contract is
            # that it never raises to its caller, and dropped from
            # db_open_connections so a leak here would show on /api/health.
            _release_direct_connection(conn)

    def log_exception(
        self,
        request_id: str,
        project_id: str | None,
        action: str,
        exc: Exception,
        *,
        target_node_id: str | None = None,
        duration_ms: int | None = None,
        details: dict | None = None,
    ) -> None:
        self.log_audit(
            request_id=request_id,
            project_id=project_id,
            action=action,
            target_node_id=target_node_id,
            success=False,
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
            error_message=str(exc),
            details=details,
        )
        self._file.error(f"{action} failed: {exc}", extra={"request_id": request_id})


def get_audit_logger() -> AuditLogger:
    global _audit_singleton
    path = resolve_db_path()
    if _audit_singleton is None or _audit_singleton.db_path != path:
        _audit_singleton = AuditLogger(db_path=path)
    return _audit_singleton
