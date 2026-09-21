"""Concurrency locks for overlapping Red-Hat audit Celery tasks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    from .connection import init_db
except ImportError:
    from db.connection import init_db

try:
    from ..history import get_db
except ImportError:
    from history import get_db


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def fetch_lock(project_id: str) -> dict[str, Any] | None:
    init_db()
    db = get_db()
    row = db.execute(
        "SELECT project_id, active_task_id, generation, updated_at "
        "FROM redhat_audit_locks WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "project_id": row["project_id"],
        "active_task_id": row["active_task_id"],
        "generation": int(row["generation"] or 0),
        "updated_at": row["updated_at"],
    }


def bump_generation(project_id: str) -> tuple[int, str | None]:
    """Increment generation and return (new_generation, previous_task_id)."""
    init_db()
    db = get_db()
    row = fetch_lock(project_id)
    prev_task = (row or {}).get("active_task_id") or ""
    generation = int((row or {}).get("generation") or 0) + 1
    db.execute(
        """
        INSERT INTO redhat_audit_locks (project_id, active_task_id, generation, updated_at)
        VALUES (?, '', ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            generation = excluded.generation,
            updated_at = excluded.updated_at
        """,
        (project_id, generation, _now()),
    )
    db.commit()
    return generation, prev_task or None


def set_active_task(project_id: str, task_id: str, generation: int) -> None:
    init_db()
    db = get_db()
    db.execute(
        """
        UPDATE redhat_audit_locks
        SET active_task_id = ?, generation = ?, updated_at = ?
        WHERE project_id = ?
        """,
        (task_id, generation, _now(), project_id),
    )
    db.commit()


def is_stale(project_id: str, generation: int) -> bool:
    lock = fetch_lock(project_id)
    if not lock:
        return True
    return int(lock.get("generation") or 0) != int(generation)
