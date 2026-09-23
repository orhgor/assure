"""Evidence runs — founder workbench Phase 1."""

from __future__ import annotations

import json
import uuid
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


def _row_to_run(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "directive": row["directive"],
        "content": json.loads(row["content"] or "{}"),
        "model": row["model"],
        "sources_used": json.loads(row["sources_used"] or "[]"),
        "extracted_locks": json.loads(row["extracted_locks"] or "[]"),
        "status": row["status"],
        "unanchored": bool(
            row["status"] == "draft" and not json.loads(row["sources_used"] or "[]")
        ),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def insert_run(
    *,
    directive: str,
    content: dict[str, Any],
    model: str,
    sources_used: list[dict[str, Any]],
    extracted_locks: list[dict[str, Any]],
    status: str,
    workspace_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    init_db()
    db = get_db()
    # runs.workspace_id references projects(id): a run for a workspace with no
    # project row raised ForeignKeyViolation → 500 (2026-09-23). Create it the
    # way every /api/projects/<id> route does.
    if workspace_id:
        try:
            from .jdf_repository import ensure_project as _ensure_project
        except ImportError:
            from db.jdf_repository import ensure_project as _ensure_project
        _ensure_project(workspace_id)
    rid = run_id or f"run_{uuid.uuid4().hex[:12]}"
    now = _now()
    db.execute(
        """
        INSERT INTO runs (
            id, workspace_id, directive, content, model,
            sources_used, extracted_locks, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rid,
            workspace_id,
            directive,
            json.dumps(content),
            model,
            json.dumps(sources_used),
            json.dumps(extracted_locks),
            status,
            now,
            now,
        ),
    )
    db.commit()
    return fetch_run(rid)  # type: ignore[return-value]


def fetch_run(run_id: str) -> dict[str, Any] | None:
    init_db()
    db = get_db()
    row = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        return None
    return _row_to_run(row)


def list_runs(*, workspace_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    init_db()
    db = get_db()
    if workspace_id:
        rows = db.execute(
            """
            SELECT * FROM runs
            WHERE workspace_id = ? OR (workspace_id IS NULL AND ? = 'default')
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (workspace_id, workspace_id, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_run(r) for r in rows]


def delete_run(run_id: str) -> bool:
    init_db()
    db = get_db()
    cur = db.execute("DELETE FROM runs WHERE id = ?", (run_id,))
    db.commit()
    return cur.rowcount > 0
