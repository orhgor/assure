"""``model_calls``: one row per model request (schema v37, 2026-09-28).

Written by ``services/model_calls`` from litellm's callbacks (and directly by
the Bedrock Converse path); read by ``GET /api/projects/<id>/pipeline-activity``
and ``/health``. Every column is something the request produced — the model
string litellm was given, the HTTP status or exception it got back, the
measured latency, the token counts the provider returned. ``created_at`` is an
ISO-8601 text timestamp with microseconds (sortable; ``DATETIME`` would round
to the second and tie the calls of one stage).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from ..db.connection import init_db
    from ..history import borrowed_connection, get_db
except ImportError:  # pragma: no cover - flat-import fallback
    from db.connection import init_db  # type: ignore
    from history import borrowed_connection, get_db  # type: ignore

COLUMNS = (
    "id", "project_id", "stage", "stage_source", "task", "backend", "provider", "model", "status",
    "http_status", "ms", "prompt_chars", "completion_chars", "input_tokens", "output_tokens",
    "error", "stream", "path", "worker", "created_at",
)


def _row(row: Any) -> dict[str, Any]:
    data = {col: row[i] for i, col in enumerate(COLUMNS)}
    data["stream"] = bool(data.get("stream"))
    return data


def insert(row: dict[str, Any]) -> None:
    """One statement on its own connection, committed at once: the callback
    runs on the calling thread (a web request, often a long SSE stream, or a
    worker task) and must not join that request's transaction — ``db_scope``
    inside a request is the request's handle, committed at teardown, so a
    compile that streams for a minute would show no calls until it ended and a
    rolled-back one would show none at all. The ledger is evidence of what
    was sent, whatever became of the request."""
    init_db()
    values = tuple(row.get(col) for col in COLUMNS)
    placeholders = ", ".join("?" for _ in COLUMNS)
    with borrowed_connection() as db:
        db.execute(f"INSERT INTO model_calls ({', '.join(COLUMNS)}) VALUES ({placeholders})", values)
        db.commit()


def list_for_project(project_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    db = get_db()
    rows = db.execute(
        f"SELECT {', '.join(COLUMNS)} FROM model_calls WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
        (project_id, int(limit)),
    ).fetchall()
    return [_row(r) for r in rows]


def list_recent(*, limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    db = get_db()
    rows = db.execute(
        f"SELECT {', '.join(COLUMNS)} FROM model_calls ORDER BY created_at DESC LIMIT ?", (int(limit),)
    ).fetchall()
    return [_row(r) for r in rows]


def summary(*, hours: int = 24, project_id: str | None = None) -> dict[str, Any]:
    """Calls / failures in the window and the newest call's timestamp."""
    init_db()
    db = get_db()
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="microseconds")
    if project_id:
        row = db.execute(
            "SELECT COUNT(*), SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END), MAX(created_at) "
            "FROM model_calls WHERE created_at >= ? AND project_id = ?",
            (since, project_id),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT COUNT(*), SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END), MAX(created_at) "
            "FROM model_calls WHERE created_at >= ?",
            (since,),
        ).fetchone()
    return {"calls": int(row[0] or 0), "failed": int(row[1] or 0), "last_call_at": row[2], "hours": hours}


def stage_rollup(project_id: str) -> dict[str, dict[str, Any]]:
    """Per stage: call count, ok/failed, newest call's model / ms / status / error."""
    init_db()
    db = get_db()
    rows = db.execute(
        f"SELECT {', '.join(COLUMNS)} FROM model_calls WHERE project_id = ? ORDER BY created_at ASC", (project_id,)
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = _row(r)
        stage = d.get("stage") or "unknown"
        agg = out.setdefault(stage, {"calls": 0, "ok": 0, "failed": 0, "model": None, "last_at": None, "last_ms": None,
                                     "last_http_status": None, "last_error": None, "first_at": None})
        agg["calls"] += 1
        agg["ok" if d["status"] == "ok" else "failed"] += 1
        agg["model"] = d["model"]
        agg["last_at"] = d["created_at"]
        agg["first_at"] = agg["first_at"] or d["created_at"]
        agg["last_ms"] = d["ms"]
        agg["last_http_status"] = d["http_status"]
        agg["last_error"] = d["error"]
    return out
