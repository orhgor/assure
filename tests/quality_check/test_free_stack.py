"""Compare model-pair selection for the Difference Engine (Bedrock)."""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def orchestrator_module(monkeypatch):
    import prompt_matrix.llm.orchestrator as mod

    monkeypatch.delenv("ASSURE_USE_FREE_MODELS", raising=False)
    mod._assure_router = None
    importlib.reload(mod)
    yield mod
    mod._assure_router = None


def test_free_flag_retired(orchestrator_module, monkeypatch) -> None:
    """The OpenRouter free stack was removed on 2026-10-01; the flag is ignored."""
    monkeypatch.setenv("ASSURE_USE_FREE_MODELS", "1")
    monkeypatch.delenv("ASSURE_LLM_BACKEND", raising=False)
    model_a, model_b = orchestrator_module.get_compare_pair()
    assert orchestrator_module.get_active_model_stack() == "production"
    assert model_a.startswith("bedrock/") and model_b.startswith("bedrock/")


def test_production_stack_selection(orchestrator_module, monkeypatch) -> None:
    monkeypatch.delenv("ASSURE_LLM_BACKEND", raising=False)
    monkeypatch.setenv("ASSURE_BEDROCK_MODEL_B", "anthropic.claude-opus-5-5")
    model_a, model_b = orchestrator_module.get_compare_pair()
    assert model_a != model_b
    assert model_b.endswith("anthropic.claude-opus-5-5")
    assert orchestrator_module.get_active_model_stack() == "production"
