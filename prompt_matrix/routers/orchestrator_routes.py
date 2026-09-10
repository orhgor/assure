"""Multi-model orchestrator API — Difference Engine (Claude + DeepSeek staging slots)."""

from __future__ import annotations

import os

from flask import jsonify, request

try:
    from ..middleware import project_ownership_required
    from ..services.compare_models import run_compare_pair
except ImportError:
    from middleware import project_ownership_required
    from services.compare_models import run_compare_pair


def _mock_orchestrate_payload(intent: str) -> dict:
    """Fallback when provider keys are missing (local CI / offline)."""
    _ = intent
    return {
        "status": "success",
        "stack": "mock",
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


def _provider_keys_configured() -> bool:
    try:
        from ..llm.orchestrator import get_compare_pair, use_free_models
    except ImportError:
        from llm.orchestrator import get_compare_pair, use_free_models

    model_a, model_b = get_compare_pair()
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    groq_key = os.getenv("GROQ_API_KEY")

    def _has_key(model: str) -> bool:
        provider = model.split("/")[0].lower()
        if provider == "gemini":
            return bool(gemini_key)
        if provider == "deepseek":
            return bool(deepseek_key)
        if provider == "anthropic":
            return bool(anthropic_key)
        if provider == "openai":
            return bool(openai_key)
        if provider == "groq":
            return bool(groq_key)
        return False

    if use_free_models():
        return _has_key(model_a) and _has_key(model_b)
    return bool(anthropic_key and deepseek_key)


def _live_orchestrate_payload(intent: str) -> dict:
    try:
        from ..llm.orchestrator import get_active_model_stack
    except ImportError:
        from llm.orchestrator import get_active_model_stack

    result = run_compare_pair(intent)
    models = result.get("models") or {}
    claude = models.get("claude") or {}
    deepseek = models.get("deepseek") or {}
    if not claude.get("text") and not deepseek.get("text"):
        raise RuntimeError("both models failed")
    return {
        "status": "success",
        "stack": get_active_model_stack(),
        "models": models,
    }


def register_orchestrator_routes(app) -> None:
    @app.post("/api/projects/<project_id>/orchestrate")
    @project_ownership_required
    def orchestrate_multi_model(project_id: str):
        data = request.get_json(silent=True) or {}
        intent = str(data.get("intent") or "").strip()
        if not intent:
            return jsonify({"status": "error", "error": "intent is required"}), 400

        if _provider_keys_configured():
            try:
                payload = _live_orchestrate_payload(intent)
            except Exception as exc:
                payload = _mock_orchestrate_payload(intent)
                payload["stack"] = "mock"
                payload["warning"] = str(exc)
        else:
            payload = _mock_orchestrate_payload(intent)

        payload["project_id"] = project_id
        payload["intent"] = intent
        return jsonify(payload)
