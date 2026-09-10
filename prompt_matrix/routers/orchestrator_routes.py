"""Multi-model orchestrator API — Sprint 1 mock (Claude + DeepSeek side-by-side)."""

from __future__ import annotations

from flask import jsonify, request

try:
    from ..middleware import project_ownership_required
except ImportError:
    from middleware import project_ownership_required


def _mock_orchestrate_payload(intent: str) -> dict:
    """Sprint 1 stub — replaced by Celery multi-model task in Sprint 1 follow-up."""
    _ = intent
    return {
        "status": "success",
        "models": {
            "claude": {
                "name": "Claude 3.5 Sonnet",
                "text": (
                    "Based on the policy analysis, the aggregate liability limit is "
                    "$5,000,000 with standard exclusions."
                ),
            },
            "deepseek": {
                "name": "DeepSeek V3",
                "text": (
                    "Based on the policy analysis, the aggregate liability limit is "
                    "$4,500,000 with standard exclusions and weather sub-limits."
                ),
            },
        },
    }


def register_orchestrator_routes(app) -> None:
    @app.post("/api/projects/<project_id>/orchestrate")
    @project_ownership_required
    def orchestrate_multi_model(project_id: str):
        data = request.get_json(silent=True) or {}
        intent = str(data.get("intent") or "").strip()
        if not intent:
            return jsonify({"status": "error", "error": "intent is required"}), 400
        payload = _mock_orchestrate_payload(intent)
        payload["project_id"] = project_id
        payload["intent"] = intent
        return jsonify(payload)
