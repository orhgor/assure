"""Founder workbench — multi-pass Red-Hat audit trigger and telemetry polling.

``schedule_redhat_after_compile`` (2026-09-27) is the one place a Red-Hat
multipass audit is scheduled for a compiled draft: the manual
``POST …/redhat/auto`` route and the compile stream (``routers/draft.py``, the
``redhat`` frame) both call it, so the telemetry a reader polls at
``GET …/redhat/status`` moves from ``idle`` to ``pending`` the same way for
both. ``ASSURE_REDHAT_AUTO`` (default on) gates the automatic call only; the
button always works.
"""

from __future__ import annotations

import html
import os
from typing import Any

from flask import jsonify, request

try:
    from ..db.drafts_repository import fetch_draft
    from ..db.jdf_repository import fetch_jdf_at_version, list_jdf_revisions
    from ..db.redhat_telemetry_repository import fetch_telemetry, reset_telemetry, upsert_telemetry
    from ..middleware import project_ownership_required
    from ..signals import schedule_redhat_multipass
except ImportError:
    from db.drafts_repository import fetch_draft
    from db.jdf_repository import fetch_jdf_at_version, list_jdf_revisions
    from db.redhat_telemetry_repository import fetch_telemetry, reset_telemetry, upsert_telemetry
    from middleware import project_ownership_required
    from signals import schedule_redhat_multipass

NO_BROKER_MESSAGE = "no task broker configured"


def redhat_auto_enabled() -> bool:
    """``ASSURE_REDHAT_AUTO``: run the multipass audit after every successful
    compile without a click. Default on — until 2026-09-27 the compile stream
    always reported ``redhat.status: "skipped"`` and the audit needed the
    button, which a reader expecting "verified" never pressed."""
    raw = os.environ.get("ASSURE_REDHAT_AUTO", "").strip().lower()
    if not raw:
        return True
    return raw in ("1", "true", "yes", "on")


def _previous_revision(workspace_id: str) -> dict[str, Any] | None:
    revisions = list_jdf_revisions(workspace_id, limit=1)
    if not revisions:
        return None
    prev_version = int(revisions[0].get("version") or 0) - 1
    if prev_version < 1:
        return None
    return fetch_jdf_at_version(workspace_id, prev_version)


def schedule_redhat_after_compile(
    project_id: str,
    run_id: str | None = None,
    *,
    current_jdf: dict[str, Any] | None = None,
) -> str | None:
    """Reset the project's Red-Hat telemetry to ``pending`` and enqueue the
    multipass audit over ``current_jdf`` (the stored draft when not given)
    against the previous stored revision. Returns the Celery task id, or None
    when there is nothing to audit or no broker — in which case the telemetry
    says ``error: no task broker configured`` rather than spinning at pending.

    Scheduling twice for one compile is safe: ``signals._enqueue_multipass``
    bumps the audit generation and revokes the previous task, so a call with the
    persisted tree after a call with the pre-persist tree simply supersedes it.
    """
    workspace_id = (project_id or "").strip() or "founder"
    document = current_jdf
    if not isinstance(document, dict) or not document:
        draft = fetch_draft(workspace_id)
        document = (draft or {}).get("content") if draft else None
    if not isinstance(document, dict) or not document.get("body"):
        return None
    run_id = str(run_id).strip() if run_id else None

    reset_telemetry(workspace_id, run_id=run_id or "")
    upsert_telemetry(workspace_id, status="pending", pass1_complete=False, pass2_running=False)
    task_id = schedule_redhat_multipass(
        workspace_id,
        document,
        _previous_revision(workspace_id),
        run_id=run_id,
        debounce=False,
    )
    if not task_id:
        # ``schedule_redhat_multipass`` returns None when the Celery broker is
        # disabled or ``.delay`` failed. Nothing will ever advance the telemetry,
        # so ``pending`` here was a spinner that never stopped (staging,
        # 2026-09-22: no CELERY_BROKER_URL, status polled at ``pending``
        # indefinitely). Recorded as an error.
        upsert_telemetry(
            workspace_id,
            status="error",
            pass1_complete=False,
            pass2_running=False,
            error=NO_BROKER_MESSAGE,
        )
        return None
    upsert_telemetry(workspace_id, task_id=task_id)
    return task_id


def _patch_html(suggested_fix: str) -> str:
    fix = (suggested_fix or "").strip()
    if not fix:
        return ""
    return f'<span class="diff-add">{html.escape(fix)}</span>'


def _status_payload(telemetry: dict[str, Any] | None) -> dict[str, Any]:
    tel = telemetry or {}
    status_name = str(tel.get("status") or "idle")
    return {
        "pass1_complete": bool(tel.get("pass1_complete")),
        "pass2_running": bool(tel.get("pass2_running")),
        "complete": status_name == "complete" or bool(tel.get("complete")),
        "error": str(tel.get("error") or ""),
        "state": status_name,
    }


def register_redhat_routes(app) -> None:
    @app.post("/api/projects/<project_id>/redhat/auto")
    @project_ownership_required
    def trigger_redhat_auto(project_id: str):
        workspace_id = project_id.strip() or "founder"
        draft = fetch_draft(workspace_id)
        current_jdf = (draft or {}).get("content") if draft else None
        if not isinstance(current_jdf, dict) or not current_jdf:
            return jsonify({"ok": False, "error": "No draft content to audit"}), 400

        run_id = (request.get_json(silent=True) or {}).get("run_id")
        run_id = str(run_id).strip() if run_id else None

        task_id = schedule_redhat_after_compile(workspace_id, run_id, current_jdf=current_jdf)
        telemetry = fetch_telemetry(workspace_id) or reset_telemetry(workspace_id)
        if not task_id:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": NO_BROKER_MESSAGE,
                        "status": _status_payload(telemetry),
                        "findings": [],
                    }
                ),
                503,
            )
        return (
            jsonify(
                {
                    "ok": True,
                    "status": _status_payload(telemetry),
                    "findings": telemetry.get("findings") or [],
                }
            ),
            202,
        )

    @app.get("/api/projects/<project_id>/redhat/status")
    @project_ownership_required
    def redhat_status(project_id: str):
        workspace_id = project_id.strip() or "founder"
        telemetry = fetch_telemetry(workspace_id)
        if not telemetry:
            return jsonify(
                {
                    "ok": True,
                    "status": {
                        "pass1_complete": False,
                        "pass2_running": False,
                        "complete": False,
                        "error": "",
                        "state": "idle",
                    },
                    "findings": [],
                }
            )
        return jsonify(
            {
                "ok": True,
                "status": _status_payload(telemetry),
                "findings": telemetry.get("findings") or [],
            }
        )
