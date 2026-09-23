"""POST /api/projects/<id>/polish — grammar/flow rewrite with lock-pill preservation."""

from __future__ import annotations

from typing import Any

from flask import jsonify, request
from pydantic import BaseModel, ConfigDict

try:
    from ..middleware import project_ownership_required
    from ..services.polish_document import run_polish_document
except ImportError:
    from middleware import project_ownership_required
    from services.polish_document import run_polish_document


class PolishPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document: dict[str, Any]
    use_llm: bool = True


def register_polish_routes(app) -> None:
    @app.post("/api/projects/<project_id>/polish")
    @project_ownership_required
    def polish_main_document(project_id: str):
        try:
            payload = PolishPayload.model_validate(request.get_json(silent=True) or {})
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 400
        if not payload.document:
            return jsonify({"status": "error", "error": "document is required"}), 400
        try:
            result = run_polish_document(payload.document, use_llm=payload.use_llm)
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 400
        if not result.get("strict_preservation"):
            return (
                jsonify(
                    {
                        "status": "error",
                        "error": (
                            result.get("preservation_note")
                            or "strict_preservation failed: lock pills or numeric tokens were altered"
                        ),
                        "strict_preservation": False,
                        "preserved": False,
                        "lock_hashes": result.get("lock_hashes") or [],
                        # The original document, not the corrupted rewrite.
                        "document": result.get("document"),
                    }
                ),
                422,
            )
        return jsonify(
            {
                "status": "success",
                "project_id": project_id,
                "document": result["document"],
                "plain_before": result["plain_before"],
                "plain_after": result["plain_after"],
                "strict_preservation": True,
                "preserved": True,
                "lock_hashes": result["lock_hashes"],
                "lock_count": result["lock_count"],
            }
        )
