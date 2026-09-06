"""Persist keyword-level vault disagreements."""

from __future__ import annotations

import uuid
from typing import Any

try:
    from ..db.connection import init_db
    from ..history import get_db
except ImportError:
    from db.connection import init_db
    from history import get_db


def replace_project_conflicts(project_id: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    init_db()
    db = get_db()
    db.execute("DELETE FROM source_conflicts WHERE project_id = ?", (project_id,))
    stored: list[dict[str, Any]] = []
    for row in rows:
        cid = str(row.get("id") or f"scf-{uuid.uuid4().hex[:16]}")
        payload = {
            "id": cid,
            "project_id": project_id,
            "claim_id": str(row.get("claim_id") or ""),
            "source_a_id": str(row.get("source_a_id") or ""),
            "source_b_id": str(row.get("source_b_id") or ""),
            "conflict_description": str(row.get("conflict_description") or ""),
        }
        db.execute(
            """
            INSERT INTO source_conflicts (
                id, project_id, claim_id, source_a_id, source_b_id, conflict_description
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                payload["id"],
                project_id,
                payload["claim_id"],
                payload["source_a_id"],
                payload["source_b_id"],
                payload["conflict_description"],
            ),
        )
        stored.append(payload)
    db.commit()
    return list_conflicts(project_id)


def list_conflicts(project_id: str) -> list[dict[str, Any]]:
    init_db()
    db = get_db()
    rows = db.execute(
        """
        SELECT id, project_id, claim_id, source_a_id, source_b_id,
               conflict_description, detected_at
        FROM source_conflicts
        WHERE project_id = ?
        ORDER BY detected_at DESC
        """,
        (project_id,),
    ).fetchall()
    return [
        {
            "id": row[0],
            "project_id": row[1],
            "claim_id": row[2],
            "source_a_id": row[3],
            "source_b_id": row[4],
            "conflict_description": row[5],
            "detected_at": row[6],
            "naive": True,
        }
        for row in rows
    ]
