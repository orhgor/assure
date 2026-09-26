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

The review queue (``list_queue``, spec §9 item 18) and the analytics
distributions (``quality_histogram``, ``issue_distribution``, ``by_modality``,
``trend``) read the scalar columns where one exists and fall back to
``report_json`` for what has no column (modality, quality flags, per-field
reasons). No column was added for them: the rows per project are in the
hundreds, one scan per page load measured 4 ms for 200 reports on 2026-09-25,
and a migration for a report-shaped page was not worth a schema bump. SLA
arithmetic (``overdue``, ``due_words``) is done in Python against ``_now_dt`` so
tests can pin the clock.
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


def _parse_dt(value: Any) -> datetime | None:
    """Timestamp text or datetime → aware UTC datetime; None when unparsable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("T", " ").replace("Z", "")
        dt = None
        for fmt in (_TS, "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hours_words(due_at: Any, now: datetime | None = None) -> tuple[bool, str | None]:
    """(overdue, "due in 31 h" | "overdue by 2 h") for an open dispute."""
    due = _parse_dt(due_at)
    if due is None:
        return False, None
    now = now or _now_dt()
    seconds = (due - now).total_seconds()
    hours = int(abs(seconds) // 3600)
    if seconds >= 0:
        return False, ("due in under an hour" if hours < 1 else f"due in {hours} h")
    return True, ("overdue by under an hour" if hours < 1 else f"overdue by {hours} h")


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


def find_report(report_id: str) -> dict[str, Any] | None:
    """The report for a bare ``report_id`` (its ``project_id`` is inside).

    The server-rendered record page (``GET /parsing/<report_id>``) is reached
    from a link that carries only the report id; the project is a property of
    the report, not of the URL. Callers still run the ownership check on the
    returned ``project_id`` before rendering anything.
    """
    init_db()
    row = get_db().execute(
        "SELECT report_json, report_id, created_at, updated_at, project_id FROM parsure_reports WHERE report_id = ?",
        (report_id,),
    ).fetchone()
    report = _load(row)
    if report is not None:
        report["project_id"] = row[4]
    return report


def report_page_texts(project_id: str, report_id: str) -> list[str] | None:
    """The per-page text the extractor ran on, for the reviewer's own record page.

    ``_page_texts`` is a private working key: ``public_report`` strips it from
    every API response and export, and that stays so. The record page at
    ``/parsing/<report_id>`` is server-rendered for the project's own reviewer
    and is the one place the text is shown — the client asked (2026-09-25)
    where the extracted content can be read in bulk, and a value without its
    page is not reviewable. ``None`` when the report is missing or was saved
    without texts (pre-V1 rows, Textract bundles that kept no page text); the
    page then says so instead of showing an empty page.
    """
    report = get_report(project_id, report_id)
    if not report:
        return None
    texts = report.get("_page_texts")
    if not isinstance(texts, list) or not texts:
        return None
    return [str(t or "") for t in texts]


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
        "overdue": False, "due_words": _hours_words(due, opened)[1],
    }


def get_dispute(project_id: str, dispute_id: str) -> dict[str, Any] | None:
    init_db()
    row = get_db().execute(
        "SELECT dispute_id, report_id, project_id, field_name, reason, status, opened_at, due_at, resolved_at, resolution, actor "
        "FROM parsure_disputes WHERE dispute_id = ? AND project_id = ?",
        (dispute_id, project_id),
    ).fetchone()
    return _dispute_row(row) if row else None


def _dispute_row(r: Any, now: datetime | None = None) -> dict[str, Any]:
    status = r[5]
    overdue, words = _hours_words(r[7], now) if status == "open" else (False, None)
    return {
        "dispute_id": r[0], "report_id": r[1], "project_id": r[2], "field_name": r[3], "reason": r[4],
        "status": status, "opened_at": _ts(r[6]), "due_at": _ts(r[7]), "resolved_at": _ts(r[8]),
        "resolution": r[9], "actor": r[10], "sla_hours": int(DISPUTE_SLA.total_seconds() // 3600),
        "overdue": overdue, "due_words": words,
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
    now = _now_dt()
    return [_dispute_row(r, now) for r in rows]


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


# Field reason → one of the categories the analytics list and the queue's
# "Why" column speak in. Matched on the reason text the decision policy writes
# (services/field_extractor.apply_decision_policy) and on the field's own
# quality records, so a renamed reason string surfaces as "other", never as a
# wrong category.
REASON_CATEGORIES = (
    ("disputed", "Disputed"),
    ("not_found", "Not found"),
    ("signature", "Signature"),
    ("number_quality", "Number quality"),
    ("plausibility", "Plausibility"),
    ("verification", "Verification"),
    ("compliance", "Compliance-bound"),
    ("low_confidence", "Low confidence"),
    ("other", "Other"),
)
_REASON_LABELS = dict(REASON_CATEGORIES)

QUALITY_BUCKETS = (
    ("0.0–0.2", 0.0, 0.2),
    ("0.2–0.4", 0.2, 0.4),
    ("0.4–0.6", 0.4, 0.6),
    ("0.6–0.8", 0.6, 0.8),
    ("0.8–1.0", 0.8, 1.0),
)

TREND_DAYS = 30


def reason_category(field: dict[str, Any]) -> str:
    reason = str(field.get("reason") or "").lower()
    if field.get("field_state") == "disputed" or reason.startswith("disputed"):
        return "disputed"
    if field.get("field_type") == "signature" or reason.startswith("signature") or (field.get("signature_quality") or {}).get("review_required"):
        return "signature"
    if field.get("value") is None or "not found" in reason:
        return "not_found"
    if "number_quality" in reason or (field.get("number_quality") or {}).get("review_required") or reason.startswith("vin rejected"):
        return "number_quality"
    if "plausibility" in reason or field.get("plausibility_violation"):
        return "plausibility"
    if "z3" in reason or field.get("z3_violation"):
        return "verification"
    if "compliance" in reason or (field.get("compliance_bound") and field.get("field_state") != "accepted"):
        return "compliance"
    if "confidence" in reason or "could not be parsed" in reason:
        return "low_confidence"
    return "other"


def _needs_attention(field: dict[str, Any]) -> bool:
    routing = str(field.get("routing_action") or "none").lower()
    return routing != "none" or field.get("field_state") in ("disputed", "rejected")


def _num(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Review queue (spec §9 item 18; brief "Parsure flow" step 4)
# --------------------------------------------------------------------------

def list_queue(project_id: str, *, limit: int = 200) -> dict[str, Any]:
    """Every field across the project's reports that still asks for a person.

    A field is queued when its ``routing_action`` is not ``none`` or its
    ``field_state`` is ``disputed`` / ``rejected`` (spec §5: the routing action
    is the operational ask; a rejected field with no routing still needs a
    decision recorded against it). Newest report first; within a report an
    overdue dispute comes first, then other disputes, then the rest in the
    report's own field order. ``counts`` are counts of the queued items.
    """
    init_db()
    limit = max(1, min(int(limit), 2000))
    now = _now_dt()
    reports = list_reports(project_id, limit=1000)
    open_disputes: dict[tuple[str, str], dict[str, Any]] = {}
    for d in list_disputes(project_id, status="open"):
        open_disputes.setdefault((str(d["report_id"]), str(d["field_name"])), d)

    items: list[dict[str, Any]] = []
    counts = {"needs_review": 0, "disputed": 0, "overdue": 0, "rejected": 0}
    for report in reports:  # already newest first
        report_id = str(report.get("report_id"))
        document_type = (report.get("classification") or {}).get("document_type")
        group: list[tuple[int, int, dict[str, Any]]] = []
        for order, field in enumerate(report.get("fields") or []):
            if not _needs_attention(field):
                continue
            state = str(field.get("field_state") or "unverified")
            dispute = open_disputes.get((report_id, str(field.get("name"))))
            dispute_view = None
            if dispute is not None:
                dispute_view = {
                    "dispute_id": dispute["dispute_id"],
                    "due_at": dispute["due_at"],
                    "overdue": bool(dispute["overdue"]),
                    "due_words": dispute["due_words"],
                    "reason": dispute.get("reason"),
                }
            rank = 0 if (dispute_view and dispute_view["overdue"]) else (1 if state == "disputed" else 2)
            if state == "disputed":
                counts["disputed"] += 1
                if dispute_view and dispute_view["overdue"]:
                    counts["overdue"] += 1
            elif state == "rejected":
                counts["rejected"] += 1
            else:
                counts["needs_review"] += 1
            group.append((rank, order, {
                "report_id": report_id,
                "document_id": report.get("document_id"),
                "filename": report.get("filename"),
                "document_type": document_type,
                "field_name": field.get("name"),
                "label": field.get("label") or field.get("name"),
                "value": field.get("value"),
                "extraction_confidence": _num(field.get("extraction_confidence")),
                "reason": field.get("reason"),
                "reason_category": reason_category(field),
                "field_state": state,
                "routing_action": str(field.get("routing_action") or "none"),
                "dispute": dispute_view,
                "created_at": report.get("created_at"),
            }))
        group.sort(key=lambda t: (t[0], t[1]))
        items.extend(item for _, _, item in group)
    return {"items": items[:limit], "counts": counts, "total": len(items), "now": now.strftime(_TS)}


# --------------------------------------------------------------------------
# Analytics (spec §9 item 27; brief §4 "premium report, not BI wall")
# --------------------------------------------------------------------------

def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def analytics(project_id: str) -> dict[str, Any]:
    """Counts, rates and distributions over this project's reports.

    Counters and the histogram come from the scalar columns in SQL; modality,
    quality flags and field reasons live only in ``report_json`` and are read
    from one scan of the project's rows. Rates are ``None`` (not 0) when their
    denominator is zero; the trend carries every one of the last 30 days so a
    day without intake is an explicit gap, not a missing point.
    """
    init_db()
    db = get_db()
    now = _now_dt()
    row = db.execute(
        "SELECT COUNT(*), AVG(document_quality_score), COALESCE(SUM(fields_total), 0), COALESCE(SUM(fields_review), 0), "
        "COALESCE(SUM(fields_rejected), 0), COALESCE(SUM(replay_eligible), 0) FROM parsure_reports WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    by_type_rows = db.execute(
        "SELECT COALESCE(document_type, 'uncertain'), COUNT(*) FROM parsure_reports WHERE project_id = ? GROUP BY COALESCE(document_type, 'uncertain')",
        (project_id,),
    ).fetchall()
    histogram_rows = db.execute(
        """
        SELECT CASE
                 WHEN document_quality_score < 0.2 THEN 0
                 WHEN document_quality_score < 0.4 THEN 1
                 WHEN document_quality_score < 0.6 THEN 2
                 WHEN document_quality_score < 0.8 THEN 3
                 ELSE 4
               END AS bucket, COUNT(*)
        FROM parsure_reports WHERE project_id = ? AND document_quality_score IS NOT NULL
        GROUP BY 1
        """,
        (project_id,),
    ).fetchall()
    unscored = db.execute(
        "SELECT COUNT(*) FROM parsure_reports WHERE project_id = ? AND document_quality_score IS NULL", (project_id,)
    ).fetchone()[0]
    corrections = db.execute(
        "SELECT COUNT(*) FROM parsure_corrections WHERE project_id = ?", (project_id,)
    ).fetchone()[0]
    dispute_rows = db.execute(
        "SELECT due_at FROM parsure_disputes WHERE project_id = ? AND status = 'open'", (project_id,)
    ).fetchall()
    disputes_open = len(dispute_rows)
    disputes_overdue = 0
    disputes_due_24h = 0
    for (due_at,) in dispute_rows:
        due = _parse_dt(due_at)
        if due is None:
            continue
        if due < now:
            disputes_overdue += 1
        elif due - now <= timedelta(hours=24):
            disputes_due_24h += 1

    # What has no column: modality, quality flags, field reasons, day of intake.
    since = (now - timedelta(days=TREND_DAYS - 1)).replace(hour=0, minute=0, second=0)
    scan = db.execute(
        "SELECT report_json, created_at, document_quality_score, fields_review FROM parsure_reports WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    by_modality: dict[str, int] = {}
    flag_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    days: dict[str, dict[str, Any]] = {}
    for offset in range(TREND_DAYS):
        day = (since + timedelta(days=offset)).strftime("%Y-%m-%d")
        days[day] = {"day": day, "documents": 0, "quality_sum": 0.0, "quality_n": 0, "fields_review": 0}
    for report_json, created_at, quality, fields_review in scan:
        try:
            report = json.loads(report_json) if isinstance(report_json, str) else (report_json or {})
        except ValueError:
            report = {}
        modality = str(report.get("modality") or "unknown")
        by_modality[modality] = by_modality.get(modality, 0) + 1
        flags = {str(f) for f in (report.get("quality_flags") or [])}
        if not flags:
            flags = {str(f) for p in (report.get("pages") or []) for f in (p.get("flags") or [])}
        for flag in flags:
            flag_counts[flag] = flag_counts.get(flag, 0) + 1
        for field in report.get("fields") or []:
            if _needs_attention(field):
                cat = reason_category(field)
                reason_counts[cat] = reason_counts.get(cat, 0) + 1
        created = _parse_dt(created_at)
        if created is not None:
            day = created.strftime("%Y-%m-%d")
            bucket = days.get(day)
            if bucket is not None:
                bucket["documents"] += 1
                bucket["fields_review"] += int(fields_review or 0)
                q = _num(quality)
                if q is not None:
                    bucket["quality_sum"] += q
                    bucket["quality_n"] += 1

    hist_by_bucket = {int(r[0]): int(r[1]) for r in histogram_rows}
    quality_histogram = [
        {"bucket": label, "low": low, "high": high, "count": hist_by_bucket.get(i, 0)}
        for i, (label, low, high) in enumerate(QUALITY_BUCKETS)
    ]
    trend = [
        {
            "day": d["day"],
            "documents": d["documents"],
            "avg_quality": round(d["quality_sum"] / d["quality_n"], 3) if d["quality_n"] else None,
            "fields_review": d["fields_review"],
        }
        for d in days.values()
    ]
    documents = int(row[0] or 0)
    fields_total = int(row[2] or 0)
    fields_review = int(row[3] or 0)
    avg_quality = row[1]
    return {
        "documents": documents,
        "by_document_type": {str(r[0]): int(r[1]) for r in by_type_rows},
        "by_modality": dict(sorted(by_modality.items(), key=lambda kv: (-kv[1], kv[0]))),
        "avg_document_quality": round(float(avg_quality), 3) if avg_quality is not None else None,
        "fields_total": fields_total,
        "fields_review": fields_review,
        "fields_rejected": int(row[4] or 0),
        "disputes_open": disputes_open,
        "disputes_overdue": disputes_overdue,
        "disputes_due_24h": disputes_due_24h,
        "corrections": int(corrections or 0),
        "replay_eligible": int(row[5] or 0),
        "review_rate": _rate(fields_review, fields_total),
        "correction_rate": _rate(int(corrections or 0), fields_total),
        "replay_eligible_rate": _rate(int(row[5] or 0), documents),
        "quality_histogram": quality_histogram,
        "quality_unscored": int(unscored or 0),
        "issue_distribution": {
            "quality_flags": [
                {"key": k, "label": k.replace("_", " ").capitalize(), "count": v}
                for k, v in sorted(flag_counts.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
            "field_reasons": [
                {"key": k, "label": _REASON_LABELS.get(k, k), "count": v}
                for k, v in sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
        },
        "trend": trend,
        "trend_days": TREND_DAYS,
        "golden_accuracy": golden_accuracy(),
    }
