"""User feedback persistence."""

from __future__ import annotations

import uuid
from typing import Any

try:
    from ..db.connection import init_db
    from ..history import get_db
except ImportError:
    from db.connection import init_db
    from history import get_db


def save_feedback(
    *,
    user_email: str,
    message: str,
    rating: int | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    init_db()
    db = get_db()
    row_id = f"fb-{uuid.uuid4().hex[:16]}"
    db.execute(
        """
        INSERT INTO feedback (id, user_email, message, rating, url)
        VALUES (?, ?, ?, ?, ?)
        """,
        (row_id, user_email, message, rating, url or ""),
    )
    db.commit()
    return {
        "id": row_id,
        "user_email": user_email,
        "message": message,
        "rating": rating,
        "url": url or "",
    }


def list_feedback(*, limit: int = 200) -> list[dict[str, Any]]:
    init_db()
    db = get_db()
    cap = max(1, min(int(limit), 500))
    rows = db.execute(
        """
        SELECT id, user_email, message, rating, url, created_at
        FROM feedback
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (cap,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        out.append(
            {
                "id": item.get("id"),
                "user_email": item.get("user_email") or "",
                "message": item.get("message") or "",
                "rating": item.get("rating"),
                "url": item.get("url") or "",
                "created_at": item.get("created_at"),
            }
        )
    return out
