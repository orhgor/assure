"""Parsure intake reports, corrections, disputes and the review audit log.

Tables (schema v32, ``db/connection._migrate_v32``): ``parsure_reports`` holds
one JSON report per parsed document (the versioned multimodal contract built
by ``services/v1_orchestrator``) plus the scalar columns listing/analytics
read; ``parsure_corrections`` and ``parsure_disputes`` are the reviewer's
record against a field; ``parsure_audit_events`` is the append-only log of
the ten review event types (spec §9 item 22 — it says "SQLite" there; this
repo is PostgreSQL-only by hard rule, so the log is a PostgreSQL table
written through the same ``?``-placeholder dialect ``pg_compat`` translates).

``report_json`` may carry ``_page_texts`` (the per-page text the extractor
ran on) so a classification override can re-extract without the original
bytes. Every reader that hands a report outward strips keys beginning with
``_`` (``public_report``) — page text is evidence for the reviewer's own
project, not part of the exported contract.

Counters in ``analytics`` are counts of rows; ``golden_accuracy`` is read
from ``tests/golden/last_run.json`` (what ``scripts/validate_golden_set.py``
last measured) and is ``None`` when that file does not exist. Nothing here
estimates a number it did not count.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from ..db.connection import init_db
    from ..history import get_db
except ImportError:
    from db.connection import init_db
    from history import get_db

EVENT_TYPES = (
    "intake_received",
    "quality_assessed",
    "classified",
    "fields_extracted",
    "decision_applied",
    "field_accepted",
    "field_corrected",
    "dispute_opened",
    "dispute_resolved",
    "classification_overridden",
    "exported",
)

#: Spec §3: dispute workflow with a 72-hour SLA.
DISPUTE_SLA = timedelta(hours=72)

_TS = "%Y-%m-%d %H:%M:%S"


def _now_dt() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _now() -> str:
    return _now_dt().strftime(_TS)


def _ts(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime(_TS)
    return str(value)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def public_report(report: dict[str, Any]) -> dict[str, Any]:
    """The report without its private ``_``-prefixed working keys."""
    return {k: v for k, v in report.items() if not str(k).startswith("_")}


def _review_counts(report: dict[str, Any]) -> tuple[int, int, int]:
    summary = report.get("review_summary") or {}
    fields = report.get("fields") or []
    total = int(summary.get("fields_total") if summary.get("fields_total") is not None else len(fields))
    review = int(summary.get("fields_review") if summary.get("fields_review") is not None else sum(1 for f in fields if f.get("review_required")))
    rejected = int(summary.get("fields_rejected") if summary.get("fields_rejected") is not None else sum(1 for f in fields if f.get("field_state") == "rejected"))
    return total, review, rejected


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------

def save_report(project_id: str, report: dict[str, Any]) -> str:
    """Insert the report; a retry with the same ``report_id`` updates in place."""
    init_db()
    db = get_db()
    report_id = str(report.get("report_id") or f"pr-{uuid.uuid4().hex[:16]}")
    report["report_id"] = report_id
    report["project_id"] = project_id
    now = _now()
    report.setdefault("created_at", now)
    total, review, rejected = _review_counts(report)
    db.execute(
        """
        INSERT INTO parsure_reports (
            report_id, project_id, document_id, revision_id, job_id, filename, document_type,
            document_quality_score, fields_total, fields_review, fields_rejected, replay_eligible,
            report_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_id) DO UPDATE SET
            document_type = excluded.document_type,
            document_quality_score = excluded.document_quality_score,
            fields_total = excluded.fields_total,
            fields_review = excluded.fields_review,
            fields_rejected = excluded.fields_rejected,
            replay_eligible = excluded.replay_eligible,
            report_json = excluded.report_json,
            updated_at = excluded.updated_at
        """,
        (
            report_id, project_id, report.get("document_id"), report.get("revision_id"), report.get("job_id"),
            str(report.get("filename") or ""), (report.get("classification") or {}).get("document_type"),
            report.get("document_quality_score"), total, review, rejected,
            1 if (report.get("replay") or {}).get("eligible") else 0,
            _dumps(report), report["created_at"], now,
        ),
    )
    db.commit()
    return report_id


def update_report(project_id: str, report_id: str, report: dict[str, Any]) -> bool:
    """Replace the stored report (after a correction, dispute, override)."""
    init_db()
    db = get_db()
    report["report_id"] = report_id
    report["updated_at"] = _now()
    total, review, rejected = _review_counts(report)
    cur = db.execute(
        """
        UPDATE parsure_reports SET document_type = ?, document_quality_score = ?, fields_total = ?,
            fields_review = ?, fields_rejected = ?, replay_eligible = ?, report_json = ?, updated_at = ?
        WHERE report_id = ? AND project_id = ?
        """,
        (
            (report.get("classification") or {}).get("document_type"), report.get("document_quality_score"),
            total, review, rejected, 1 if (report.get("replay") or {}).get("eligible") else 0,
            _dumps(report), report["updated_at"], report_id, project_id,
        ),
    )
    db.commit()
    return bool(cur.rowcount)


def _load(row: Any) -> dict[str, Any] | None:
    if not row:
        return None
    try:
        report = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    if isinstance(report, dict):
        report["report_id"] = row[1]
        report["created_at"] = _ts(row[2]) or report.get("created_at")
        report["updated_at"] = _ts(row[3]) or report.get("updated_at")
    return report if isinstance(report, dict) else None


def get_report(project_id: str, report_id: str) -> dict[str, Any] | None:
    init_db()
    row = get_db().execute(
        "SELECT report_json, report_id, created_at, updated_at FROM parsure_reports WHERE report_id = ? AND project_id = ?",
        (report_id, project_id),
    ).fetchone()
    return _load(row)


def get_latest_report(project_id: str) -> dict[str, Any] | None:
    init_db()
    row = get_db().execute(
        "SELECT report_json, report_id, created_at, updated_at FROM parsure_reports WHERE project_id = ? "
        "ORDER BY created_at DESC, report_id DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    return _load(row)


def list_reports(project_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """Full report dicts, newest first."""
    init_db()
    limit = max(1, min(int(limit), 1000))
    rows = get_db().execute(
        "SELECT report_json, report_id, created_at, updated_at FROM parsure_reports WHERE project_id = ? "
        "ORDER BY created_at DESC, report_id DESC LIMIT ?",
        (project_id, limit),
    ).fetchall()
    out = []
    for row in rows:
        report = _load(row)
        if report:
            out.append(report)
    return out


# --------------------------------------------------------------------------
# Corrections
# --------------------------------------------------------------------------

def record_correction(
    project_id: str,
    report_id: str,
    field_name: str,
    *,
    original_value: Any,
    corrected_value: Any,
    actor: str | None,
    reason: str | None,
) -> int:
    init_db()
    db = get_db()
    now = _now()
    cur = db.execute(
        """
        INSERT INTO parsure_corrections (report_id, project_id, field_name, original_value, corrected_value, actor, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (report_id, project_id, field_name, _dumps(original_value), _dumps(corrected_value), actor, reason, now),
    )
    db.commit()
    return int(cur.lastrowid or 0)


def _loads(value: Any) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def list_corrections(project_id: str, report_id: str, field_name: str | None = None) -> list[dict[str, Any]]:
    init_db()
    sql = (
        "SELECT id, report_id, project_id, field_name, original_value, corrected_value, actor, reason, created_at "
        "FROM parsure_corrections WHERE project_id = ? AND report_id = ?"
    )
    params: list[Any] = [project_id, report_id]
    if field_name:
        sql += " AND field_name = ?"
        params.append(field_name)
    sql += " ORDER BY created_at ASC, id ASC"
    rows = get_db().execute(sql, tuple(params)).fetchall()
    return [
        {
            "id": r[0], "report_id": r[1], "project_id": r[2], "field_name": r[3],
            "original_value": _loads(r[4]), "corrected_value": _loads(r[5]),
            "actor": r[6], "reason": r[7], "created_at": _ts(r[8]),
        }
        for r in rows
    ]


# --------------------------------------------------------------------------
# Disputes (72 h SLA)
# --------------------------------------------------------------------------

def open_dispute(project_id: str, report_id: str, field_name: str, *, reason: str | None, actor: str | None) -> dict[str, Any]:
    init_db()
    db = get_db()
    opened = _now_dt()
    due = opened + DISPUTE_SLA
    dispute_id = f"dsp-{uuid.uuid4().hex[:16]}"
    db.execute(
        """
        INSERT INTO parsure_disputes (dispute_id, report_id, project_id, field_name, reason, status, opened_at, due_at, actor)
        VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?)
        ON CONFLICT(dispute_id) DO NOTHING
        """,
        (dispute_id, report_id, project_id, field_name, reason, opened.strftime(_TS), due.strftime(_TS), actor),
    )
    db.commit()
    return {
        "dispute_id": dispute_id, "report_id": report_id, "project_id": project_id, "field_name": field_name,
        "reason": reason, "status": "open", "opened_at": opened.strftime(_TS), "due_at": due.strftime(_TS),
        "resolved_at": None, "resolution": None, "actor": actor, "sla_hours": int(DISPUTE_SLA.total_seconds() // 3600),
    }


def get_dispute(project_id: str, dispute_id: str) -> dict[str, Any] | None:
    init_db()
    row = get_db().execute(
        "SELECT dispute_id, report_id, project_id, field_name, reason, status, opened_at, due_at, resolved_at, resolution, actor "
        "FROM parsure_disputes WHERE dispute_id = ? AND project_id = ?",
        (dispute_id, project_id),
    ).fetchone()
    return _dispute_row(row) if row else None


def _dispute_row(r: Any) -> dict[str, Any]:
    return {
        "dispute_id": r[0], "report_id": r[1], "project_id": r[2], "field_name": r[3], "reason": r[4],
        "status": r[5], "opened_at": _ts(r[6]), "due_at": _ts(r[7]), "resolved_at": _ts(r[8]),
        "resolution": r[9], "actor": r[10], "sla_hours": int(DISPUTE_SLA.total_seconds() // 3600),
    }


def resolve_dispute(project_id: str, dispute_id: str, *, resolution: str | None, actor: str | None) -> dict[str, Any] | None:
    init_db()
    db = get_db()
    now = _now()
    db.execute(
        "UPDATE parsure_disputes SET status = 'resolved', resolved_at = ?, resolution = ?, actor = COALESCE(?, actor) "
        "WHERE dispute_id = ? AND project_id = ? AND status = 'open'",
        (now, resolution, actor, dispute_id, project_id),
    )
    db.commit()
    return get_dispute(project_id, dispute_id)


def list_disputes(project_id: str, report_id: str | None = None, status: str | None = None, field_name: str | None = None) -> list[dict[str, Any]]:
    init_db()
    sql = (
        "SELECT dispute_id, report_id, project_id, field_name, reason, status, opened_at, due_at, resolved_at, resolution, actor "
        "FROM parsure_disputes WHERE project_id = ?"
    )
    params: list[Any] = [project_id]
    if report_id:
        sql += " AND report_id = ?"
        params.append(report_id)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if field_name:
        sql += " AND field_name = ?"
        params.append(field_name)
    sql += " ORDER BY opened_at ASC, dispute_id ASC"
    rows = get_db().execute(sql, tuple(params)).fetchall()
    return [_dispute_row(r) for r in rows]


# --------------------------------------------------------------------------
# Audit events
# --------------------------------------------------------------------------

def log_event(
    project_id: str,
    event_type: str,
    *,
    report_id: str | None = None,
    field_name: str | None = None,
    actor: str | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown parsure event type: {event_type}")
    init_db()
    db = get_db()
    cur = db.execute(
        "INSERT INTO parsure_audit_events (project_id, report_id, event_type, field_name, actor, payload_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (project_id, report_id, event_type, field_name, actor, _dumps(payload or {}), _now()),
    )
    db.commit()
    return int(cur.lastrowid or 0)


def list_events(project_id: str, *, report_id: str | None = None, field_name: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    init_db()
    limit = max(1, min(int(limit), 2000))
    sql = "SELECT id, project_id, report_id, event_type, field_name, actor, payload_json, created_at FROM parsure_audit_events WHERE project_id = ?"
    params: list[Any] = [project_id]
    if report_id:
        sql += " AND report_id = ?"
        params.append(report_id)
    if field_name:
        sql += " AND field_name = ?"
        params.append(field_name)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    rows = get_db().execute(sql, tuple(params)).fetchall()
    return [
        {
            "id": r[0], "project_id": r[1], "report_id": r[2], "event_type": r[3], "field_name": r[4],
            "actor": r[5], "payload": _loads(r[6]) or {}, "created_at": _ts(r[7]),
        }
        for r in rows
    ]


# --------------------------------------------------------------------------
# Analytics
# --------------------------------------------------------------------------

def _golden_results_path() -> Path:
    override = os.environ.get("PARSURE_GOLDEN_RESULTS", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "tests" / "golden" / "last_run.json"


def golden_accuracy() -> dict[str, Any] | None:
    """The last golden-set validation result, or None when it was never run."""
    path = _golden_results_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {
        "field_accuracy": data.get("field_accuracy"),
        "type_accuracy": data.get("type_accuracy"),
        "documents": data.get("documents"),
        "fields_checked": data.get("fields_checked"),
        "run_at": data.get("run_at"),
        "source": str(path),
    }


def analytics(project_id: str) -> dict[str, Any]:
    init_db()
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*), AVG(document_quality_score), COALESCE(SUM(fields_total), 0), COALESCE(SUM(fields_review), 0), "
        "COALESCE(SUM(fields_rejected), 0), COALESCE(SUM(replay_eligible), 0) FROM parsure_reports WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    by_type_rows = db.execute(
        "SELECT COALESCE(document_type, 'uncertain'), COUNT(*) FROM parsure_reports WHERE project_id = ? GROUP BY COALESCE(document_type, 'uncertain')",
        (project_id,),
    ).fetchall()
    disputes_open = db.execute(
        "SELECT COUNT(*) FROM parsure_disputes WHERE project_id = ? AND status = 'open'", (project_id,)
    ).fetchone()[0]
    corrections = db.execute(
        "SELECT COUNT(*) FROM parsure_corrections WHERE project_id = ?", (project_id,)
    ).fetchone()[0]
    avg_quality = row[1]
    return {
        "documents": int(row[0] or 0),
        "by_document_type": {str(r[0]): int(r[1]) for r in by_type_rows},
        "avg_document_quality": round(float(avg_quality), 3) if avg_quality is not None else None,
        "fields_total": int(row[2] or 0),
        "fields_review": int(row[3] or 0),
        "fields_rejected": int(row[4] or 0),
        "disputes_open": int(disputes_open or 0),
        "corrections": int(corrections or 0),
        "replay_eligible": int(row[5] or 0),
        "golden_accuracy": golden_accuracy(),
    }
