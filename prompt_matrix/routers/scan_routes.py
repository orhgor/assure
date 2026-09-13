"""POST /api/projects/<id>/scan — full-context Main document analysis."""

from __future__ import annotations

from typing import Any

from flask import jsonify, request
from pydantic import BaseModel, ConfigDict

try:
    from ..middleware import project_ownership_required
    from ..services.full_context_scan import run_full_context_scan
except ImportError:
    from middleware import project_ownership_required
    from services.full_context_scan import run_full_context_scan


class ScanPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document: dict[str, Any]


def register_scan_routes(app) -> None:
    @app.post("/api/projects/<project_id>/scan")
    @project_ownership_required
    def full_context_scan(project_id: str):
        try:
            payload = ScanPayload.model_validate(request.get_json(silent=True) or {})
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 400
        if not payload.document:
            return jsonify({"status": "error", "error": "document is required"}), 400
        try:
            result = run_full_context_scan(payload.document)
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 400
        return jsonify(
            {
                "status": "success",
                "project_id": project_id,
                "issues": result["issues"],
                "issue_count": result["issue_count"],
                "scanned_nodes": result["scanned_nodes"],
            }
        )
