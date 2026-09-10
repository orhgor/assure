"""POST /api/runs/compare — parallel two-model Difference Engine dispatch."""

from __future__ import annotations

import asyncio

from flask import jsonify, request

try:
    from ..llm.orchestrator import get_active_model_stack
    from ..services.compare_models import run_compare_pair_async
except ImportError:
    from llm.orchestrator import get_active_model_stack
    from services.compare_models import run_compare_pair_async


def _normalize_model_result(result: dict, model_name: str) -> dict:
    if isinstance(result, Exception):
        return {"model": model_name, "error": str(result), "jdf": None}
    if result.get("error"):
        return {
            "model": result.get("model") or model_name,
            "error": result.get("error"),
            "jdf": None,
        }
    return result


def register_compare_routes(app) -> None:
    @app.post("/api/runs/compare")
    def compare_models():
        payload = request.get_json(silent=True) or {}
        intent = str(payload.get("intent") or "").strip()
        source_ids = payload.get("source_ids") or []
        if not intent:
            return jsonify({"error": "intent is required"}), 400
        if not isinstance(source_ids, list):
            source_ids = []

        try:
            result = asyncio.run(run_compare_pair_async(intent, source_ids))
        except Exception as exc:
            return jsonify({"error": f"orchestrator failed: {exc}"}), 500

        model_a = _normalize_model_result(result.get("model_a") or {}, "model_a")
        model_b = _normalize_model_result(result.get("model_b") or {}, "model_b")

        return jsonify(
            {
                "status": "success",
                "stack": get_active_model_stack(),
                "model_a": model_a,
                "model_b": model_b,
                "models": result.get("models") or {},
            }
        ), 200
