"""Rotating file logger and SQLite audit trail (fail-safe)."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:
    from ..history import DB_PATH
    from ..paths import user_data_dir
except ImportError:
    from history import DB_PATH
    from paths import user_data_dir

_audit_singleton: AuditLogger | None = None


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return True


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
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.execute("PRAGMA busy_timeout=5000;")
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
            conn.close()
        except Exception as exc:
            self._file.error(
                f"Audit log failed (SQLite): {exc}",
                extra={"request_id": request_id},
            )
            if error_message:
                self._file.error(
                    f"Original action: {action}, error: {error_message}",
                    extra={"request_id": request_id},
                )

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
    if _audit_singleton is None:
        _audit_singleton = AuditLogger()
    return _audit_singleton
