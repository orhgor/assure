"""CI/CD drift-check endpoint."""

from __future__ import annotations

import uuid

from flask import jsonify, request
from pydantic import BaseModel, Field

try:
    from ..db.jdf_repository import ensure_project
    from ..db.substrate_repository import fetch_substrate_entries_by_ids
    from ..lib.logger import get_audit_logger
    from ..middleware import project_ownership_required
    from ..services.drift_check import run_drift_check
except ImportError:
    from db.jdf_repository import ensure_project
    from db.substrate_repository import fetch_substrate_entries_by_ids
    from lib.logger import get_audit_logger
    from middleware import project_ownership_required
    from services.drift_check import run_drift_check


class DriftCheckPayload(BaseModel):
    config: dict = Field(default_factory=dict)
    policy_id: str | None = None
    policy_text: str | None = None


def register_drift_routes(app) -> None:
    try:
        from ..rate_limits import limiter
    except ImportError:
        from rate_limits import limiter

    @app.post("/api/projects/<project_id>/drift-check")
    @limiter.limit("30 per minute")
    @project_ownership_required
    def drift_check(project_id: str):
        request_id = str(uuid.uuid4())
        audit = get_audit_logger()
        ensure_project(project_id)
        data = request.get_json(silent=True) or {}
        try:
            payload = DriftCheckPayload.model_validate(data)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        if not payload.config:
            return jsonify({"ok": False, "error": "config is required"}), 400

        policy_text = (payload.policy_text or "").strip()
        if payload.policy_id:
            entries = fetch_substrate_entries_by_ids(project_id, [payload.policy_id.strip()])
            if not entries:
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": f"Policy document '{payload.policy_id}' not found in vault.",
                        }
                    ),
                    404,
                )
            policy_text = str(entries[0].get("extracted_text") or "")

        if not policy_text:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Provide policy_id (vault file) or policy_text.",
                    }
                ),
                400,
            )

        result = run_drift_check(payload.config, policy_text)
        audit.log_audit(
            request_id,
            project_id,
            "DRIFT_CHECK",
            success=result.get("status") != "FAIL",
            details={
                "status": result.get("status"),
                "drift_detected": result.get("drift_detected"),
                "finding_count": len(result.get("findings") or []),
            },
        )
        return jsonify({"ok": True, **result})
