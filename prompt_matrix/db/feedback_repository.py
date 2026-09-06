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
