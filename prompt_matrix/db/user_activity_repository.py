"""Global user activity audit log (v1.5)."""

from __future__ import annotations

import json
import uuid
from typing import Any

try:
    from ..db.connection import init_db
    from ..history import get_db
except ImportError:
    from db.connection import init_db
    from history import get_db


def log_user_activity(
    user_id: str,
    action: str,
    *,
    project_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_db()
    db = get_db()
    row_id = f"ual-{uuid.uuid4().hex[:16]}"
    db.execute(
        """
        INSERT INTO user_activity_log (id, user_id, project_id, action, details)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            row_id,
            user_id,
            project_id,
            action,
            json.dumps(details or {}),
        ),
    )
    db.commit()
    return {"id": row_id, "user_id": user_id, "project_id": project_id, "action": action}


def list_user_activity(
    *,
    user_id: str | None = None,
    project_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    init_db()
    db = get_db()
    clauses: list[str] = []
    params: list[Any] = []
    if user_id:
        clauses.append("user_id = ?")
        params.append(user_id)
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(int(limit))
    rows = db.execute(
        f"""
        SELECT id, user_id, project_id, action, details, timestamp
        FROM user_activity_log
        {where}
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            details = json.loads(row[4] or "{}")
        except json.JSONDecodeError:
            details = {}
        out.append(
            {
                "id": row[0],
                "user_id": row[1],
                "project_id": row[2],
                "action": row[3],
                "details": details,
                "timestamp": row[5],
            }
        )
    return out
