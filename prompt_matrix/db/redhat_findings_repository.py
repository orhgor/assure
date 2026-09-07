"""Red-Hat adversarial findings for founder workbench runs."""

from __future__ import annotations

import json
import uuid
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


def _row_to_finding(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "title": row["title"],
        "content": row["content"],
        "severity": row["severity"],
        "suggested_fix": row["suggested_fix"],
        "status": row["status"],
        "dismissal_rationale": row["dismissal_rationale"],
        "model_used": row["model_used"],
        "highlight_text": row["highlight_text"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def insert_findings(
    run_id: str,
    findings: list[dict[str, Any]],
    *,
    model_used: str = "",
) -> list[dict[str, Any]]:
    init_db()
    db = get_db()
    now = _now()
    out: list[dict[str, Any]] = []
    for item in findings:
        fid = f"rh_{uuid.uuid4().hex[:12]}"
        db.execute(
            """
            INSERT INTO redhat_findings (
                id, run_id, title, content, severity, suggested_fix,
                status, dismissal_rationale, model_used, highlight_text,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'open', '', ?, ?, ?, ?)
            """,
            (
                fid,
                run_id,
                str(item.get("title") or "Finding"),
                str(item.get("content") or ""),
                str(item.get("severity") or "medium"),
                str(item.get("suggested_fix") or ""),
                model_used,
                str(item.get("highlight_text") or item.get("highlight") or ""),
                now,
                now,
            ),
        )
        out.append(fetch_finding(fid))  # type: ignore[arg-type]
    db.commit()
    return [f for f in out if f]


def fetch_finding(finding_id: str) -> dict[str, Any] | None:
    init_db()
    db = get_db()
    row = db.execute("SELECT * FROM redhat_findings WHERE id = ?", (finding_id,)).fetchone()
    if not row:
        return None
    return _row_to_finding(row)


def list_findings_for_run(run_id: str) -> list[dict[str, Any]]:
    init_db()
    db = get_db()
    rows = db.execute(
        """
        SELECT * FROM redhat_findings
        WHERE run_id = ?
        ORDER BY created_at ASC
        """,
        (run_id,),
    ).fetchall()
    return [_row_to_finding(r) for r in rows]


def list_findings_for_workspace(workspace_id: str) -> list[dict[str, Any]]:
    init_db()
    db = get_db()
    rows = db.execute(
        """
        SELECT f.* FROM redhat_findings f
        JOIN runs r ON r.id = f.run_id
        WHERE r.workspace_id = ? OR (r.workspace_id IS NULL AND ? = 'default')
        ORDER BY f.created_at ASC
        """,
        (workspace_id, workspace_id),
    ).fetchall()
    return [_row_to_finding(r) for r in rows]


def update_finding_status(
    finding_id: str,
    *,
    status: str,
    dismissal_rationale: str = "",
    suggested_fix: str | None = None,
) -> dict[str, Any] | None:
    init_db()
    db = get_db()
    now = _now()
    if suggested_fix is not None:
        db.execute(
            """
            UPDATE redhat_findings
            SET status = ?, dismissal_rationale = ?, suggested_fix = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, dismissal_rationale, suggested_fix, now, finding_id),
        )
    else:
        db.execute(
            """
            UPDATE redhat_findings
            SET status = ?, dismissal_rationale = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, dismissal_rationale, now, finding_id),
        )
    db.commit()
    return fetch_finding(finding_id)
