"""Per-node revision snapshots (distinct from document-level jdf_revisions)."""

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


def save_node_revision(
    project_id: str,
    node_id: str,
    node_json: dict[str, Any],
    *,
    document_version: int | None = None,
    mutation_type: str = "NODE_UPDATE",
    change_summary: str | None = None,
) -> dict[str, Any]:
    init_db()
    db = get_db()
    row = db.execute(
        """
        SELECT COALESCE(MAX(version), 0) FROM node_revisions
        WHERE project_id = ? AND node_id = ?
        """,
        (project_id, node_id),
    ).fetchone()
    next_version = int(row[0]) + 1
    revision_id = f"nrev-{uuid.uuid4().hex[:16]}"
    db.execute(
        """
        INSERT INTO node_revisions (
            id, project_id, node_id, version, node_json,
            document_version, mutation_type, change_summary
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            revision_id,
            project_id,
            node_id,
            next_version,
            json.dumps(node_json),
            document_version,
            mutation_type,
            change_summary,
        ),
    )
    db.commit()
    return {
        "ok": True,
        "revision_id": revision_id,
        "node_id": node_id,
        "version": next_version,
        "document_version": document_version,
    }


def list_node_revisions(project_id: str, node_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    db = get_db()
    rows = db.execute(
        """
        SELECT id, version, document_version, mutation_type, change_summary, created_at, node_json
        FROM node_revisions
        WHERE project_id = ? AND node_id = ?
        ORDER BY version DESC
        LIMIT ?
        """,
        (project_id, node_id, int(limit)),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            node = json.loads(r[6]) if r[6] else {}
        except json.JSONDecodeError:
            node = {}
        preview = ""
        if isinstance(node, dict):
            preview = str(node.get("content") or node.get("title") or node.get("caption") or "")[
                :160
            ]
        out.append(
            {
                "revision_id": r[0],
                "version": int(r[1]),
                "document_version": r[2],
                "mutation_type": r[3],
                "change_summary": r[4],
                "created_at": r[5],
                "preview": preview,
            }
        )
    return out


def fetch_node_revision(project_id: str, node_id: str, revision_id: str) -> dict[str, Any] | None:
    init_db()
    db = get_db()
    row = db.execute(
        """
        SELECT node_json FROM node_revisions
        WHERE project_id = ? AND node_id = ? AND id = ?
        """,
        (project_id, node_id, revision_id),
    ).fetchone()
    if not row:
        return None
    try:
        node = json.loads(row[0])
    except json.JSONDecodeError:
        return None
    return node if isinstance(node, dict) else None
