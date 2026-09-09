"""Founder workbench — multi-pass Red-Hat audit trigger and telemetry polling."""

from __future__ import annotations

import html
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

        previous_jdf = None
        revisions = list_jdf_revisions(workspace_id, limit=1)
        if revisions:
            prev_version = int(revisions[0].get("version") or 0) - 1
            if prev_version >= 1:
                previous_jdf = fetch_jdf_at_version(workspace_id, prev_version)

        reset_telemetry(workspace_id, run_id=run_id or "")
        upsert_telemetry(workspace_id, status="pending", pass1_complete=False, pass2_running=False)

        task_id = schedule_redhat_multipass(
            workspace_id,
            current_jdf,
            previous_jdf,
            run_id=run_id,
            debounce=False,
        )
        if task_id:
            upsert_telemetry(workspace_id, task_id=task_id)

        telemetry = fetch_telemetry(workspace_id) or reset_telemetry(workspace_id)
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
