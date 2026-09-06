"""Project template catalog (wizard type step)."""

from __future__ import annotations

import json
from typing import Any

try:
    from ..db.connection import init_db
    from ..history import get_db
except ImportError:
    from db.connection import init_db
    from history import get_db


def list_templates() -> list[dict[str, Any]]:
    init_db()
    db = get_db()
    rows = db.execute(
        """
        SELECT id, name, jdf_structure, default_prompt, suggested_sources, created_at
        FROM project_templates
        ORDER BY name ASC
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "id": row[0],
                "name": row[1],
                "jdf_structure": json.loads(row[2] or "{}"),
                "default_prompt": row[3] or "",
                "suggested_sources": json.loads(row[4] or "[]"),
                "created_at": row[5],
            }
        )
    return out


def fetch_template(template_id: str) -> dict[str, Any] | None:
    init_db()
    db = get_db()
    row = db.execute(
        """
        SELECT id, name, jdf_structure, default_prompt, suggested_sources, created_at
        FROM project_templates WHERE id = ?
        """,
        (template_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "name": row[1],
        "jdf_structure": json.loads(row[2] or "{}"),
        "default_prompt": row[3] or "",
        "suggested_sources": json.loads(row[4] or "[]"),
        "created_at": row[5],
    }
