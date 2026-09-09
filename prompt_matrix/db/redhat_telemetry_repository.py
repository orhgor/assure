"""Live Red-Hat multi-pass audit telemetry for founder drawer polling."""

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


def _parse_findings(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def _row_to_telemetry(row: Any) -> dict[str, Any]:
    status = str(row["status"] or "pending")
    return {
        "project_id": row["project_id"],
        "task_id": row["task_id"] or "",
        "run_id": row["run_id"] or "",
        "status": status,
        "pass1_complete": bool(row["pass1_complete"]),
        "pass2_running": bool(row["pass2_running"]),
        "complete": status == "complete",
        "error": row["error"] or "",
        "findings": _parse_findings(row["findings_json"]),
        "updated_at": row["updated_at"],
    }


def fetch_telemetry(project_id: str) -> dict[str, Any] | None:
    init_db()
    db = get_db()
    row = db.execute(
        "SELECT * FROM redhat_audit_telemetry WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if not row:
        return None
    return _row_to_telemetry(row)


def upsert_telemetry(
    project_id: str,
    *,
    task_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    pass1_complete: bool | None = None,
    pass2_running: bool | None = None,
    findings: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    init_db()
    db = get_db()
    existing = fetch_telemetry(project_id) or {
        "project_id": project_id,
        "task_id": "",
        "run_id": "",
        "status": "pending",
        "pass1_complete": False,
        "pass2_running": False,
        "complete": False,
        "error": "",
        "findings": [],
    }
    merged_findings = list(existing.get("findings") or [])
    if findings is not None:
        merged_findings = findings

    payload = {
        "task_id": task_id if task_id is not None else existing.get("task_id") or "",
        "run_id": run_id if run_id is not None else existing.get("run_id") or "",
        "status": status if status is not None else existing.get("status") or "pending",
        "pass1_complete": (
            pass1_complete if pass1_complete is not None else existing.get("pass1_complete")
        ),
        "pass2_running": (
            pass2_running if pass2_running is not None else existing.get("pass2_running")
        ),
        "error": error if error is not None else existing.get("error") or "",
        "findings_json": json.dumps(merged_findings, ensure_ascii=False),
        "updated_at": _now(),
    }
    db.execute(
        """
        INSERT INTO redhat_audit_telemetry (
            project_id, task_id, run_id, status, pass1_complete, pass2_running,
            findings_json, error, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            task_id = excluded.task_id,
            run_id = excluded.run_id,
            status = excluded.status,
            pass1_complete = excluded.pass1_complete,
            pass2_running = excluded.pass2_running,
            findings_json = excluded.findings_json,
            error = excluded.error,
            updated_at = excluded.updated_at
        """,
        (
            project_id,
            payload["task_id"],
            payload["run_id"],
            payload["status"],
            1 if payload["pass1_complete"] else 0,
            1 if payload["pass2_running"] else 0,
            payload["findings_json"],
            payload["error"],
            payload["updated_at"],
        ),
    )
    db.commit()
    return fetch_telemetry(project_id) or {}


def append_findings(
    project_id: str,
    new_findings: list[dict[str, Any]],
    *,
    status: str | None = None,
    pass1_complete: bool | None = None,
    pass2_running: bool | None = None,
) -> dict[str, Any]:
    existing = fetch_telemetry(project_id) or {}
    merged = list(existing.get("findings") or [])
    seen = {
        (
            str(item.get("id") or ""),
            str(item.get("title") or "").strip().lower(),
            int(item.get("pass") or 0),
        )
        for item in merged
    }
    for item in new_findings:
        key = (
            str(item.get("id") or ""),
            str(item.get("title") or "").strip().lower(),
            int(item.get("pass") or 0),
        )
        if key in seen:
            continue
        if not item.get("id"):
            item = dict(item)
            item["id"] = f"rh_{uuid.uuid4().hex[:12]}"
        seen.add(key)
        merged.append(item)
    return upsert_telemetry(
        project_id,
        findings=merged,
        status=status,
        pass1_complete=pass1_complete,
        pass2_running=pass2_running,
    )


def reset_telemetry(project_id: str, *, task_id: str = "", run_id: str = "") -> dict[str, Any]:
    return upsert_telemetry(
        project_id,
        task_id=task_id,
        run_id=run_id,
        status="pending",
        pass1_complete=False,
        pass2_running=False,
        findings=[],
        error="",
    )
