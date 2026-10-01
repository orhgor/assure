"""Model family detection and provider timeouts for the Difference Engine."""

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
    assert orchestrator_module.family_of("bedrock/us.anthropic.claude-sonnet-5-5") == "anthropic"
    assert orchestrator_module.family_of("ollama/qwen2.5:1.5b") == "qwen"
    assert orchestrator_module.family_of("ollama/llama3.2:1b") == "meta"


def test_timeout_for_bedrock_is_generous(orchestrator_module) -> None:
    assert orchestrator_module.timeout_for("bedrock/us.anthropic.claude-sonnet-5-5") == 180
    assert orchestrator_module.timeout_for("ollama/qwen2.5:1.5b") == 600
    assert orchestrator_module.timeout_for("anthropic/claude-sonnet-4-5") == 60
