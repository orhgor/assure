"""SQLite blobs for PEM pipeline cache (AST / Red-Hat). OMP stores the index."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from ..db.connection import init_db
    from ..history import get_db
except ImportError:
    from db.connection import init_db
    from history import get_db

CACHE_TTL_DAYS = 30
_LAST_PRUNE_MONO = 0.0


def _ttl_days() -> int:
    raw = (os.environ.get("PIPELINE_CACHE_TTL_DAYS") or "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    return CACHE_TTL_DAYS


def fetch_pipeline_cache(cache_key: str) -> dict[str, Any] | None:
    if not cache_key:
        return None
    init_db()
    db = get_db()
    row = db.execute(
        """
        SELECT payload_json FROM pipeline_cache
        WHERE cache_key = ?
          AND (expires_at IS NULL OR expires_at > datetime('now'))
        """,
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
    *,
    ttl_days: int | None = None,
) -> None:
    if not cache_key or not isinstance(payload, dict):
        return
    init_db()
    days = ttl_days if ttl_days is not None else _ttl_days()
    expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    db.execute(
        """
        INSERT INTO pipeline_cache (cache_key, project_id, kind, payload_json, updated_at, expires_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            project_id = excluded.project_id,
            kind = excluded.kind,
            payload_json = excluded.payload_json,
            updated_at = CURRENT_TIMESTAMP,
            expires_at = excluded.expires_at
        """,
        (cache_key, project_id, kind, json.dumps(payload, ensure_ascii=False), expires_at),
    )
    db.commit()


def prune_expired_pipeline_cache() -> int:
    init_db()
    db = get_db()
    cur = db.execute(
        "DELETE FROM pipeline_cache WHERE expires_at IS NOT NULL AND expires_at <= datetime('now')"
    )
    db.commit()
    return int(cur.rowcount or 0)


def sqlite_cache_expired(cache_key: str) -> bool:
    """True when a SQLite cache row exists and its TTL has elapsed."""
    if not cache_key:
        return False
    init_db()
    db = get_db()
    row = db.execute(
        """
        SELECT 1 FROM pipeline_cache
        WHERE cache_key = ?
          AND expires_at IS NOT NULL
          AND expires_at <= datetime('now')
        """,
        (cache_key,),
    ).fetchone()
    return row is not None


def maybe_prune_pipeline_cache(*, min_interval_s: float = 86400) -> int:
    global _LAST_PRUNE_MONO
    now = time.monotonic()
    if _LAST_PRUNE_MONO and (now - _LAST_PRUNE_MONO) < min_interval_s:
        return 0
    deleted = prune_expired_pipeline_cache()
    _LAST_PRUNE_MONO = now
    return deleted
