"""HTTP and project-level rate limits for Assure API routes."""

from __future__ import annotations

import os
from datetime import UTC, datetime

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

try:
    from .db.connection import init_db
    from .history import get_db
except ImportError:
    from db.connection import init_db
    from history import get_db

DEFAULT_COMPILE_PER_DAY = 100


class DailyCompileLimitError(Exception):
    """Project exceeded daily compile quota."""


limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.environ.get("RATE_LIMIT_STORAGE_URI", "memory://"),
    default_limits=[],
    headers_enabled=True,
)


def init_app_limiter(app: Flask) -> None:
    """Bind limiter to Flask and register 429 handler."""
    limiter.init_app(app)

    @app.errorhandler(429)
    def _rate_limit_exceeded(exc):
        retry_after = getattr(exc, "retry_after", None)
        payload = {
            "ok": False,
            "error": "Too many requests. Please wait and try again.",
            "retry_after": retry_after,
        }
        response = jsonify(payload)
        response.status_code = 429
        if retry_after is not None:
            response.headers["Retry-After"] = str(retry_after)
        return response


def _today_utc() -> str:
    return datetime.now(UTC).date().isoformat()


def check_daily_compile_limit(project_id: str, *, max_per_day: int | None = None) -> None:
    """Raise DailyCompileLimitError when project exceeds daily compile cap."""
    cap = (
        max_per_day
        if max_per_day is not None
        else int(os.environ.get("ASSURE_COMPILE_DAILY_LIMIT", DEFAULT_COMPILE_PER_DAY))
    )
    if cap <= 0:
        return
    init_db()
    db = get_db()
    row = db.execute(
        """
        SELECT count FROM daily_compile_limits
        WHERE project_id = ? AND date = ?
        """,
        (project_id, _today_utc()),
    ).fetchone()
    if row and int(row[0]) >= cap:
        raise DailyCompileLimitError(
            f"Daily compile limit reached ({cap} per project). Try again tomorrow."
        )


def increment_daily_compile_limit(project_id: str) -> int:
    """Increment today's compile counter; return new count."""
    init_db()
    db = get_db()
    today = _today_utc()
    db.execute(
        """
        INSERT INTO daily_compile_limits (project_id, date, count)
        VALUES (?, ?, 1)
        ON CONFLICT(project_id, date) DO UPDATE SET count = count + 1
        """,
        (project_id, today),
    )
    db.commit()
    row = db.execute(
        "SELECT count FROM daily_compile_limits WHERE project_id = ? AND date = ?",
        (project_id, today),
    ).fetchone()
    return int(row[0]) if row else 1


def worker_ingest_request() -> bool:
    """True when trusted edge worker presents the ingest secret."""
    secret = (os.environ.get("SUBSTRATE_INGEST_SECRET") or "").strip()
    if not secret:
        return False
    header = (request.headers.get("X-Assure-Worker-Secret") or "").strip()
    auth = (request.headers.get("Authorization") or "").strip()
    return header == secret or auth == f"Bearer {secret}"
