"""Free vs production model-pair selection for Difference Engine."""

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


def test_free_stack_selection(orchestrator_module, monkeypatch) -> None:
    monkeypatch.setenv("ASSURE_USE_FREE_MODELS", "1")
    orchestrator_module._assure_router = None
    importlib.reload(orchestrator_module)
    model_a, model_b = orchestrator_module.get_compare_pair()
    assert model_a != model_b, "Free stack must use two different models"
    assert orchestrator_module.get_active_model_stack() == "free"
    assert model_a == "gemini/gemini-3.6-flash"
    assert model_b == "deepseek/deepseek-chat"


def test_production_stack_selection(orchestrator_module, monkeypatch) -> None:
    monkeypatch.setenv("ASSURE_USE_FREE_MODELS", "0")
    orchestrator_module._assure_router = None
    importlib.reload(orchestrator_module)
    model_a, model_b = orchestrator_module.get_compare_pair()
    assert model_a != model_b
    assert orchestrator_module.get_active_model_stack() == "production"
