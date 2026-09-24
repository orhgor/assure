"""Ingest jobs: the durable, watchable record of a document's parse pipeline.

One row per upload (``ingest_jobs``, schema v27). The web tier creates it when
it queues the work; the worker moves it through the stages
``queued → fetching → parsing → verifying → persisting → done`` (or ``failed`` /
``skipped``) and fills in what each stage learned: parser, page count, OCR
confidence, Z3 verdict and violation count, Red-Hat status, revision and OMP
artifact ids, timings, error. ``GET /api/projects/<id>/ingest-jobs`` and the
workbench's processing panel read it; ``GET /api/tasks/<task_id>`` joins it.

Every write is a single statement, so two workers or a retry cannot leave a
row half-updated, and every column is a fact the pipeline produced — nothing
here is estimated or defaulted to look finished.
"""

from __future__ import annotations

import json
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import Any

try:
    from ..db.connection import init_db
    from ..history import get_db
except ImportError:
    from db.connection import init_db
    from history import get_db

STAGES = ("queued", "fetching", "parsing", "verifying", "persisting", "done", "failed", "skipped")
ACTIVE_STATUSES = ("queued", "fetching", "parsing", "verifying", "persisting")
TERMINAL_STATUSES = ("done", "failed", "skipped")

_COLUMNS = (
    "job_id", "project_id", "kind", "filename", "object_key", "task_id", "status",
    "stage_history", "parser_name", "source_kind", "page_count", "parse_confidence",
    "ocr_confidence", "z3_status", "z3_violation_count", "redhat_status", "revision_id",
    "revision_version", "omp_artifact_id", "substrate_file_id", "error", "worker",
    "size_bytes", "duration_ms", "created_at", "started_at", "finished_at", "updated_at",
)
_UPDATABLE = {
    "task_id", "object_key", "parser_name", "source_kind", "page_count", "parse_confidence",
    "ocr_confidence", "z3_status", "z3_violation_count", "redhat_status", "revision_id",
    "revision_version", "omp_artifact_id", "substrate_file_id", "error", "worker",
    "size_bytes", "duration_ms",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _row_to_dict(row: Any) -> dict[str, Any]:
    data = {col: row[i] for i, col in enumerate(_COLUMNS)}
    try:
        data["stage_history"] = json.loads(data.get("stage_history") or "[]")
    except (TypeError, ValueError):
        data["stage_history"] = []
    data["active"] = data["status"] in ACTIVE_STATUSES
    return data


def create_job(
    project_id: str,
    *,
    kind: str,
    filename: str,
    object_key: str | None = None,
    task_id: str | None = None,
    size_bytes: int | None = None,
) -> str:
    init_db()
    db = get_db()
    job_id = f"job-{uuid.uuid4().hex[:16]}"
    now = _now()
    db.execute(
        """
        INSERT INTO ingest_jobs (job_id, project_id, kind, filename, object_key, task_id,
                                 status, stage_history, size_bytes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
        """,
        (
            job_id,
            project_id,
            kind,
            filename,
            object_key,
            task_id,
            json.dumps([{"stage": "queued", "at": now}]),
            size_bytes,
            now,
            now,
        ),
    )
    db.commit()
    return job_id


def set_task(job_id: str, task_id: str) -> None:
    init_db()
    db = get_db()
    db.execute(
        "UPDATE ingest_jobs SET task_id = ?, updated_at = ? WHERE job_id = ?",
        (task_id, _now(), job_id),
    )
    db.commit()


def advance(job_id: str, stage: str, **fields: Any) -> None:
    """Move the job to ``stage`` and record what the stage learned.

    The stage history is appended in the same statement that changes the
    status, so a reader never sees a status without its history entry.
    ``started_at`` is set on the first non-queued stage; ``finished_at`` and
    ``duration_ms`` on a terminal one.
    """
    if stage not in STAGES:
        raise ValueError(f"unknown ingest stage: {stage!r}")
    init_db()
    db = get_db()
    row = db.execute(
        "SELECT stage_history, created_at, started_at, status FROM ingest_jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    if not row:
        return
    if row[3] in TERMINAL_STATUSES and stage != "queued":
        # A finished job is final until a retry re-queues it. Without this a
        # redelivered message (acks_late + visibility expiry) ran "fetching" →
        # "skipped: staged object not found" over a `done` row whose revision
        # exists, and /api/tasks reported the document as skipped (audit
        # 2026-09-23).
        return
    try:
        history = json.loads(row[0] or "[]")
    except (TypeError, ValueError):
        history = []
    now = _now()
    history.append({"stage": stage, "at": now})
    sets = ["status = ?", "stage_history = ?", "updated_at = ?"]
    params: list[Any] = [stage, json.dumps(history), now]
    if stage != "queued" and not row[2]:
        sets.append("started_at = ?")
        params.append(now)
    if stage == "queued":
        # A retry starts the clock again: the failure's finished_at/duration
        # would otherwise make prune_jobs delete the job while it runs.
        sets.extend(["finished_at = NULL", "duration_ms = NULL", "revision_id = NULL"])
        fields.setdefault("error", None)
    if stage in TERMINAL_STATUSES:
        sets.append("finished_at = ?")
        params.append(now)
        if "duration_ms" not in fields:
            try:
                started = datetime.strptime(str(row[2] or row[1])[:19], "%Y-%m-%d %H:%M:%S")
                fields["duration_ms"] = int(
                    (datetime.now(timezone.utc).replace(tzinfo=None) - started).total_seconds() * 1000
                )
            except (TypeError, ValueError):
                pass
        if stage == "done" and "worker" not in fields:
            fields["worker"] = socket.gethostname()[:120]
    for key, value in fields.items():
        if key not in _UPDATABLE:
            continue
        sets.append(f"{key} = ?")
        params.append(value)
    params.append(job_id)
    db.execute(f"UPDATE ingest_jobs SET {', '.join(sets)} WHERE job_id = ?", tuple(params))
    db.commit()


def get_job(job_id: str) -> dict[str, Any] | None:
    init_db()
    db = get_db()
    row = db.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM ingest_jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    return _row_to_dict(row) if row else None


def get_job_by_task(task_id: str) -> dict[str, Any] | None:
    if not task_id:
        return None
    init_db()
    db = get_db()
    row = db.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM ingest_jobs WHERE task_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    return _row_to_dict(row) if row else None


_STALE_SWEEP_EVERY_S = 60.0
_last_stale_sweep = 0.0


def list_jobs(project_id: str, *, limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
    # Healing sweep at most once a minute per process: it is an UPDATE + COMMIT
    # and the shell polls this list every second while an upload runs (audit
    # 2026-09-24 — one write transaction per poll per client).
    global _last_stale_sweep
    now = time.monotonic()
    if now - _last_stale_sweep >= _STALE_SWEEP_EVERY_S:
        _last_stale_sweep = now
        try:
            mark_stale()
        except Exception:  # never let healing break the listing
            pass
    init_db()
    db = get_db()
    limit = max(1, min(int(limit), 500))
    if status == "active":
        placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
        rows = db.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM ingest_jobs WHERE project_id = ? "
            f"AND status IN ({placeholders}) ORDER BY created_at DESC LIMIT ?",
            (project_id, *ACTIVE_STATUSES, limit),
        ).fetchall()
    elif status:
        rows = db.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM ingest_jobs WHERE project_id = ? AND status = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (project_id, status, limit),
        ).fetchall()
    else:
        rows = db.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM ingest_jobs WHERE project_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def job_stats(project_id: str) -> dict[str, int]:
    """Counts per status plus the Z3 verdict tally of finished jobs."""
    init_db()
    db = get_db()
    stats = {s: 0 for s in STAGES}
    for status, count in db.execute(
        "SELECT status, COUNT(*) FROM ingest_jobs WHERE project_id = ? GROUP BY status",
        (project_id,),
    ).fetchall():
        stats[str(status)] = int(count)
    stats["active"] = sum(stats[s] for s in ACTIVE_STATUSES)
    stats["total"] = sum(stats[s] for s in STAGES)
    z3 = {"PASS": 0, "VIOLATION": 0, "TIMEOUT": 0, "ERROR": 0}
    for status, count in db.execute(
        "SELECT z3_status, COUNT(*) FROM ingest_jobs WHERE project_id = ? AND z3_status IS NOT NULL "
        "GROUP BY z3_status",
        (project_id,),
    ).fetchall():
        z3[str(status)] = int(count)
    stats["z3"] = z3  # type: ignore[assignment]
    return stats


STALE_AFTER_MINUTES = 45  # > task_time_limit (15 min) + SQS visibility (30 min)


def mark_stale(*, older_than_minutes: int = STALE_AFTER_MINUTES) -> int:
    """Fail active jobs nobody has touched for ``older_than_minutes``.

    A SIGKILLed task (task_time_limit), an OOM or a lost host never reaches
    ``advance("failed")``; the row stayed in ``parsing`` forever and the
    Processing panel polled it forever (audit 2026-09-23). Called from
    ``list_jobs`` so the panel heals itself; cheap (indexed status scan).
    """
    init_db()
    db = get_db()
    cur = db.execute(
        "UPDATE ingest_jobs SET status = 'failed', error = ?, finished_at = ?, updated_at = ? "
        "WHERE status NOT IN ('done', 'failed', 'skipped') "
        "AND updated_at < datetime('now', ?)",
        (
            f"worker lost: no progress for {int(older_than_minutes)} minutes",
            _now(),
            _now(),
            f"-{int(older_than_minutes)} minutes",
        ),
    )
    db.commit()
    return int(cur.rowcount or 0)


def prune_jobs(project_id: str | None = None, *, keep_days: int = 90) -> int:
    init_db()
    db = get_db()
    if project_id:
        cur = db.execute(
            "DELETE FROM ingest_jobs WHERE project_id = ? AND finished_at IS NOT NULL "
            "AND finished_at < datetime('now', ?)",
            (project_id, f"-{int(keep_days)} days"),
        )
    else:
        cur = db.execute(
            "DELETE FROM ingest_jobs WHERE finished_at IS NOT NULL AND finished_at < datetime('now', ?)",
            (f"-{int(keep_days)} days",),
        )
    db.commit()
    return int(cur.rowcount or 0)
