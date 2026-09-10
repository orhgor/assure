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
    assert pairs["claude"]["litellm_model"] == "gemini/gemini-3.6-flash"
    assert pairs["deepseek"]["litellm_model"] == "deepseek/deepseek-chat"
    model_list = orchestrator_module._build_model_list()
    assert model_list[0]["litellm_params"]["model"] == "deepseek/deepseek-chat"
    assert model_list[1]["litellm_params"]["model"] == "gemini/gemini-3.6-flash"
