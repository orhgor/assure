"""POST /api/runs/compare — parallel two-model Difference Engine dispatch."""

from __future__ import annotations

import asyncio

from flask import jsonify, request

try:
    from ..llm.orchestrator import family_of, get_active_model_stack, get_compare_pair
    from ..services.compare_models import run_compare_pair_async
except ImportError:
    from llm.orchestrator import family_of, get_active_model_stack, get_compare_pair
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
            model_a, model_b = get_compare_pair()
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        fam_a, fam_b = family_of(model_a), family_of(model_b)
        if fam_a == fam_b:
            return jsonify(
                {
                    "error": "Free stack requires two different model families for diff",
                    "model_a": model_a,
                    "model_b": model_b,
                    "family_a": fam_a,
                    "family_b": fam_b,
                }
            ), 500

        try:
            result = asyncio.run(run_compare_pair_async(intent, source_ids))
        except Exception as exc:
            return jsonify({"error": f"orchestrator failed: {exc}"}), 500

        used_a = (result.get("pair") or (model_a, model_b))[0]
        used_b = (result.get("pair") or (model_a, model_b))[1]
        model_a_out = _normalize_model_result(result.get("model_a") or {}, used_a)
        model_b_out = _normalize_model_result(result.get("model_b") or {}, used_b)

        return jsonify(
            {
                "status": "success",
                "stack": get_active_model_stack(),
                "model_a": model_a_out,
                "model_b": model_b_out,
                "models": result.get("models") or {},
                "families": {
                    "claude": family_of(used_a),
                    "secondary": family_of(used_b),
                },
            }
        ), 200
