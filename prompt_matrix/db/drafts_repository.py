"""Workspace drafts — founder workbench Phase 1."""

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


def _row_to_draft(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "content": json.loads(row["content"] or "{}"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def upsert_draft(*, workspace_id: str, content: dict[str, Any]) -> dict[str, Any]:
    init_db()
    db = get_db()
    now = _now()
    existing = db.execute(
        "SELECT id FROM drafts WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchone()
    if existing:
        db.execute(
            "UPDATE drafts SET content = ?, updated_at = ? WHERE workspace_id = ?",
            (json.dumps(content), now, workspace_id),
        )
        draft_id = existing["id"]
    else:
        draft_id = f"draft_{uuid.uuid4().hex[:12]}"
        db.execute(
            """
            INSERT INTO drafts (id, workspace_id, content, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (draft_id, workspace_id, json.dumps(content), now, now),
        )
    db.commit()
    return fetch_draft(workspace_id)  # type: ignore[return-value]


def fetch_draft(workspace_id: str) -> dict[str, Any] | None:
    init_db()
    db = get_db()
    row = db.execute(
        "SELECT * FROM drafts WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchone()
    if not row:
        return None
    return _row_to_draft(row)
