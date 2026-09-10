"""Staging free-model stack — ASSURE_USE_FREE_MODELS + FREE_MODEL_PAIRS."""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def orchestrator_module(monkeypatch):
    import prompt_matrix.llm.orchestrator as mod

    importlib.reload(mod)
    monkeypatch.delenv("ASSURE_USE_FREE_MODELS", raising=False)
    mod._assure_router = None
    yield mod
    mod._assure_router = None


def test_use_free_models_off_by_default(orchestrator_module) -> None:
    assert orchestrator_module.use_free_models() is False
    pairs = orchestrator_module.orchestrator_model_pairs()
    assert "claude" in pairs
    assert pairs["claude"]["litellm_model"].startswith("anthropic/")


def test_use_free_models_staging_flag(orchestrator_module, monkeypatch) -> None:
    monkeypatch.setenv("ASSURE_USE_FREE_MODELS", "1")
    orchestrator_module._assure_router = None
    assert orchestrator_module.use_free_models() is True
    pairs = orchestrator_module.orchestrator_model_pairs()
    assert pairs["claude"]["litellm_model"] == "openrouter/google/gemma-4-26b-a4b-it:free"
    assert pairs["deepseek"]["litellm_model"] == "openrouter/nvidia/nemotron-3.5-lightning:free"
    assert pairs["claude"]["family"] != pairs["deepseek"]["family"]
    model_list = orchestrator_module._build_model_list()
    models = {row["litellm_params"]["model"] for row in model_list}
    assert "openrouter/google/gemma-4-26b-a4b-it:free" in models
    assert "openrouter/nvidia/nemotron-3.5-lightning:free" in models
    names = {row["model_name"] for row in model_list}
    assert "text-reasoning" in names
    assert "table-parsing" in names
