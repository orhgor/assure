"""Per-project workspace settings on SQLite."""

from __future__ import annotations

from typing import Any

try:
    from ..db.connection import init_db
    from ..db.jdf_repository import ensure_project
    from ..history import get_db
except ImportError:
    from db.connection import init_db
    from db.jdf_repository import ensure_project
    from history import get_db


def fetch_project_settings(project_id: str) -> dict[str, Any]:
    init_db()
    ensure_project(project_id)
    db = get_db()
    row = db.execute(
        "SELECT show_citations FROM workspace_settings WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    show = bool(row[0]) if row else True
    return {"project_id": project_id, "show_citations": show}


def save_project_settings(project_id: str, *, show_citations: bool) -> dict[str, Any]:
    init_db()
    ensure_project(project_id)
    db = get_db()
    db.execute(
        """
        INSERT INTO workspace_settings (project_id, show_citations, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(project_id) DO UPDATE SET
            show_citations = excluded.show_citations,
            updated_at = datetime('now')
        """,
        (project_id, 1 if show_citations else 0),
    )
    db.commit()
    return fetch_project_settings(project_id)
