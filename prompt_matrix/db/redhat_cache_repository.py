"""Block-hash cache for multi-pass Red-Hat audits."""

from __future__ import annotations

import json
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


def fetch_cache(block_hash: str) -> dict[str, Any] | None:
    """Return cached findings payload or None on miss."""
    init_db()
    db = get_db()
    row = db.execute(
        "SELECT block_hash, findings_json, pass1_model, pass2_model, created_at "
        "FROM redhat_cache WHERE block_hash = ?",
        (block_hash,),
    ).fetchone()
    if not row:
        return None
    try:
        findings = json.loads(row["findings_json"] or "[]")
    except json.JSONDecodeError:
        findings = []
    if not isinstance(findings, list):
        findings = []
    return {
        "block_hash": row["block_hash"],
        "findings": findings,
        "pass1_model": row["pass1_model"],
        "pass2_model": row["pass2_model"],
        "created_at": row["created_at"],
    }


def save_cache(
    block_hash: str,
    findings: list[dict[str, Any]],
    *,
    pass1_model: str = "",
    pass2_model: str = "",
) -> None:
    init_db()
    db = get_db()
    db.execute(
        """
        INSERT INTO redhat_cache (block_hash, findings_json, pass1_model, pass2_model, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(block_hash) DO UPDATE SET
            findings_json = excluded.findings_json,
            pass1_model = excluded.pass1_model,
            pass2_model = excluded.pass2_model,
            created_at = excluded.created_at
        """,
        (
            block_hash,
            json.dumps(findings),
            pass1_model,
            pass2_model,
            _now(),
        ),
    )
    db.commit()
