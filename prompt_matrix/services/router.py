"""Fast heuristic router — intent classification without an LLM call."""

from __future__ import annotations

try:
    from ..services.fast_router import classify_intent
except ImportError:
    from services.fast_router import classify_intent

__all__ = ["route_intent"]


def route_intent(directive: str, *, has_sources: bool = False) -> dict[str, object]:
    """
    Return a lightweight routing decision for the Auto-Compiler orchestrator.

    Example: {"task": "draft", "has_sources": True, "intent_type": "draft"}
    """
    text = (directive or "").strip()
    intent_type = classify_intent(text)
    task = intent_type if intent_type != "unknown" else "draft"
    return {
        "task": task,
        "has_sources": bool(has_sources),
        "intent_type": intent_type,
    }
