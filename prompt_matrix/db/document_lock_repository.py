"""Document version locking for compliance sign-off."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

try:
    from ..db.connection import init_db
    from ..db.jdf_repository import current_document_version
    from ..history import get_db
except ImportError:
    from db.connection import init_db
    from db.jdf_repository import current_document_version
    from history import get_db


def hash_jdf_tree(tree: dict[str, Any]) -> str:
    payload = json.dumps(tree, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_document_lock(
    project_id: str,
    tree: dict[str, Any],
    *,
    locked_by: str,
    version: int | None = None,
) -> dict[str, Any]:
    init_db()
    db = get_db()
    ver = int(version if version is not None else current_document_version(project_id))
    content_hash = hash_jdf_tree(tree)
    row_id = f"lock-{uuid.uuid4().hex[:16]}"
    db.execute(
        """
        INSERT OR REPLACE INTO document_locks (id, project_id, version, locked_by, content_hash)
        VALUES (?, ?, ?, ?, ?)
        """,
        (row_id, project_id, ver, locked_by, content_hash),
    )
    db.commit()
    return fetch_lock_for_version(project_id, ver) or {}


def fetch_lock_for_version(project_id: str, version: int) -> dict[str, Any] | None:
    init_db()
    db = get_db()
    row = db.execute(
        """
        SELECT id, project_id, version, locked_by, locked_at, content_hash
        FROM document_locks
        WHERE project_id = ? AND version = ?
        """,
        (project_id, int(version)),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "project_id": row[1],
        "version": int(row[2]),
        "locked_by": row[3],
        "locked_at": row[4],
        "content_hash": row[5],
    }


def is_version_locked(project_id: str, version: int) -> bool:
    return fetch_lock_for_version(project_id, version) is not None


def latest_lock(project_id: str) -> dict[str, Any] | None:
    init_db()
    db = get_db()
    row = db.execute(
        """
        SELECT id, project_id, version, locked_by, locked_at, content_hash
        FROM document_locks
        WHERE project_id = ?
        ORDER BY version DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "project_id": row[1],
        "version": int(row[2]),
        "locked_by": row[3],
        "locked_at": row[4],
        "content_hash": row[5],
    }
