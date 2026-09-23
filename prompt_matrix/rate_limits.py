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


def _limiter_storage_uri() -> str:
    """Where the counters live: Redis when configured, else this process.

    ``RATE_LIMIT_STORAGE_URI`` overrides; otherwise ``REDIS_URL`` is used, so
    every web replica counts against the same bucket. ``memory://`` is only
    right for a single process and is what a missing Redis degrades to.
    """
    explicit = (os.environ.get("RATE_LIMIT_STORAGE_URI") or "").strip()
    if explicit:
        return explicit
    redis_url = (os.environ.get("REDIS_URL") or "").strip()
    if redis_url:
        return redis_url
    return "memory://"


limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=_limiter_storage_uri(),
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
    # One statement: the increment and the read-back are the same row version,
    # so two replicas incrementing at once cannot both see the same count. The
    # column is qualified because PostgreSQL treats a bare ``count`` in the
    # DO UPDATE clause as ambiguous (row vs. excluded).
    row = db.execute(
        """
        INSERT INTO daily_compile_limits (project_id, date, count)
        VALUES (?, ?, 1)
        ON CONFLICT(project_id, date) DO UPDATE
            SET count = daily_compile_limits.count + 1
        RETURNING count
        """,
        (project_id, today),
    ).fetchone()
    db.commit()
    return int(row[0]) if row else 1
