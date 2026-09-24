"""Free-stack family enforcement for Difference Engine."""

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


def test_family_detection(orchestrator_module) -> None:
    assert orchestrator_module.family_of("meta-llama/Llama-3.3-70B-Instruct") == "meta"
    assert orchestrator_module.family_of("qwen/Qwen2.5-72B-Instruct") == "qwen"
    assert orchestrator_module.family_of("mistralai/Mistral-Small") == "mistral"
    assert orchestrator_module.family_of("openrouter/google/gemma-4-26b-a4b-it:free") == "google"
    assert (
        orchestrator_module.family_of("openrouter/nvidia/nemotron-3.5-lightning:free") == "nvidia"
    )


def test_free_pair_different_families(orchestrator_module, monkeypatch) -> None:
    monkeypatch.setenv("ASSURE_USE_FREE_MODELS", "1")
    orchestrator_module._assure_router = None
    importlib.reload(orchestrator_module)
    model_a, model_b = orchestrator_module.select_free_pair()
    assert orchestrator_module.family_of(model_a) != orchestrator_module.family_of(
        model_b
    ), f"Free pair must be different families: {model_a} vs {model_b}"


def test_timeout_for_openrouter_is_generous(orchestrator_module) -> None:
    assert orchestrator_module.timeout_for("openrouter/nvidia/nemotron-3.5-lightning:free") == 180
    assert orchestrator_module.timeout_for("anthropic/claude-sonnet-4-5") == 60
