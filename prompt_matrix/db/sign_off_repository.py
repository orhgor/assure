"""Sign-off persistence for compliance review."""

from __future__ import annotations

import uuid
from typing import Any

try:
    from ..db.connection import init_db
    from ..history import get_db
except ImportError:
    from db.connection import init_db
    from history import get_db


def create_sign_off(
    project_id: str,
    *,
    reviewer_id: str,
    status: str,
    node_id: str | None = None,
    reviewer_name: str | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    init_db()
    db = get_db()
    row_id = f"so-{uuid.uuid4().hex[:16]}"
    db.execute(
        """
        INSERT INTO sign_offs (id, project_id, node_id, reviewer_id, reviewer_name, status, comment)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row_id,
            project_id,
            node_id,
            reviewer_id,
            reviewer_name or "",
            status,
            comment or "",
        ),
    )
    db.commit()
    return fetch_sign_off(row_id) or {}


def fetch_sign_off(sign_off_id: str) -> dict[str, Any] | None:
    init_db()
    db = get_db()
    row = db.execute(
        """
        SELECT id, project_id, node_id, reviewer_id, reviewer_name, status, comment, timestamp
        FROM sign_offs WHERE id = ?
        """,
        (sign_off_id,),
    ).fetchone()
    if not row:
        return None
    return _row_to_dict(row)


def list_sign_offs(project_id: str) -> list[dict[str, Any]]:
    init_db()
    db = get_db()
    rows = db.execute(
        """
        SELECT id, project_id, node_id, reviewer_id, reviewer_name, status, comment, timestamp
        FROM sign_offs
        WHERE project_id = ?
        ORDER BY timestamp DESC
        """,
        (project_id,),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": row[0],
        "project_id": row[1],
        "node_id": row[2],
        "reviewer_id": row[3],
        "reviewer_name": row[4],
        "reviewer_name_display": row[4] or row[3],
        "status": row[5],
        "comment": row[6],
        "timestamp": row[7],
    }
