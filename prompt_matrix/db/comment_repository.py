"""Project comments persisted separately from JDF content."""

from __future__ import annotations

import uuid
from typing import Any

try:
    from ..db.connection import init_db
    from ..db.jdf_repository import ensure_project, fetch_latest_jdf_or_empty
    from ..history import get_db
    from ..models.jdf import get_node_by_id
except ImportError:
    from db.connection import init_db
    from db.jdf_repository import ensure_project, fetch_latest_jdf_or_empty
    from history import get_db
    from models.jdf import get_node_by_id


def list_comments(project_id: str, *, node_id: str | None = None) -> list[dict[str, Any]]:
    init_db()
    db = get_db()
    if node_id:
        rows = db.execute(
            """
            SELECT id, project_id, node_id, author, body, created_at
            FROM project_comments
            WHERE project_id = ? AND node_id = ?
            ORDER BY created_at ASC
            """,
            (project_id, node_id),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT id, project_id, node_id, author, body, created_at
            FROM project_comments
            WHERE project_id = ?
            ORDER BY created_at DESC
            """,
            (project_id,),
        ).fetchall()
    return [
        {
            "id": r[0],
            "project_id": r[1],
            "node_id": r[2],
            "author": r[3],
            "body": r[4],
            "created_at": r[5],
        }
        for r in rows
    ]


def add_comment(
    project_id: str,
    node_id: str,
    body: str,
    *,
    author: str = "",
) -> dict[str, Any]:
    init_db()
    ensure_project(project_id)
    text = (body or "").strip()
    if not text:
        raise ValueError("comment body required")

    tree = fetch_latest_jdf_or_empty(project_id)
    if not get_node_by_id(tree, node_id):
        raise ValueError(f"node_id {node_id!r} not found in current JDF tree")

    comment_id = f"cmt-{uuid.uuid4().hex[:16]}"
    db = get_db()
    db.execute(
        """
        INSERT INTO project_comments (id, project_id, node_id, author, body)
        VALUES (?, ?, ?, ?, ?)
        """,
        (comment_id, project_id, node_id, (author or "").strip(), text),
    )
    db.commit()
    return {
        "id": comment_id,
        "project_id": project_id,
        "node_id": node_id,
        "author": author,
        "body": text,
    }


def delete_comment(project_id: str, comment_id: str) -> bool:
    init_db()
    db = get_db()
    cur = db.execute(
        "DELETE FROM project_comments WHERE project_id = ? AND id = ?",
        (project_id, comment_id),
    )
    db.commit()
    return cur.rowcount > 0
