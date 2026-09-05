"""SQLite blobs for PEM pipeline cache (AST / Red-Hat). OMP stores the index."""

from __future__ import annotations

import json
from typing import Any

try:
    from ..db.connection import init_db
    from ..history import get_db
except ImportError:
    from db.connection import init_db
    from history import get_db


def fetch_pipeline_cache(cache_key: str) -> dict[str, Any] | None:
    if not cache_key:
        return None
    init_db()
    db = get_db()
    row = db.execute(
        "SELECT payload_json FROM pipeline_cache WHERE cache_key = ?",
        (cache_key,),
    ).fetchone()
    if not row:
        return None
    try:
        parsed = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def save_pipeline_cache(
    cache_key: str,
    project_id: str,
    kind: str,
    payload: dict[str, Any],
) -> None:
    if not cache_key or not isinstance(payload, dict):
        return
    init_db()
    db = get_db()
    db.execute(
        """
        INSERT INTO pipeline_cache (cache_key, project_id, kind, payload_json, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(cache_key) DO UPDATE SET
            project_id = excluded.project_id,
            kind = excluded.kind,
            payload_json = excluded.payload_json,
            updated_at = CURRENT_TIMESTAMP
        """,
        (cache_key, project_id, kind, json.dumps(payload, ensure_ascii=False)),
    )
    db.commit()
