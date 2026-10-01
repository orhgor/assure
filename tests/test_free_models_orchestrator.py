"""Compare model pair — the retired free stack and the Bedrock default.

The OpenRouter ``:free`` stack (ASSURE_USE_FREE_MODELS=1) was removed with
OpenRouter on 2026-10-01; Compare runs the backend's two columns.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def orchestrator_module(monkeypatch):
    import prompt_matrix.llm.orchestrator as mod

    importlib.reload(mod)
    monkeypatch.delenv("ASSURE_USE_FREE_MODELS", raising=False)
    monkeypatch.delenv("ASSURE_LLM_BACKEND", raising=False)
    for var in ("ASSURE_BEDROCK_MODEL", "ASSURE_BEDROCK_MODEL_DRAFT", "ASSURE_BEDROCK_MODEL_B",
                "ASSURE_BEDROCK_MODEL_ANALYSIS", "AWS_DEFAULT_REGION", "AWS_REGION"):
        monkeypatch.delenv(var, raising=False)
    mod._assure_router = None
    yield mod
    mod._assure_router = None


def test_compare_pair_defaults_to_bedrock_sonnet(orchestrator_module) -> None:
    assert orchestrator_module.use_free_models() is False
    pairs = orchestrator_module.orchestrator_model_pairs()
    assert pairs["claude"]["litellm_model"] == "bedrock/us.anthropic.claude-sonnet-5-5"
    assert pairs["secondary"]["litellm_model"] == "bedrock/us.anthropic.claude-sonnet-5-5"
    assert pairs["claude"]["provider"] == "bedrock"
    assert pairs["claude"]["family"] == "anthropic"
    assert pairs["claude"]["name"] == "Claude Sonnet 5.5 (Bedrock)"
    assert orchestrator_module.get_compare_pair() == orchestrator_module.PRODUCTION_MODEL_PAIRS[0]


def test_leftover_free_flag_is_ignored(orchestrator_module, monkeypatch) -> None:
    monkeypatch.setenv("ASSURE_USE_FREE_MODELS", "1")
    assert orchestrator_module.use_free_models() is False
    model_a, model_b = orchestrator_module.get_compare_pair()
    assert model_a.startswith("bedrock/") and model_b.startswith("bedrock/")


def test_router_model_list_is_on_the_backend(orchestrator_module) -> None:
    model_list = orchestrator_module._build_model_list()
    names = {row["model_name"] for row in model_list}
    assert names == {"text-reasoning", "table-parsing", "vision-analysis"}
    for row in model_list:
        params = row["litellm_params"]
        assert params["model"] == "bedrock/us.anthropic.claude-sonnet-5-5"
        assert params["aws_region_name"] == "us-east-1"
        assert "api_base" not in params


def test_compare_pair_on_ollama(orchestrator_module, monkeypatch) -> None:
    monkeypatch.setenv("ASSURE_LLM_BACKEND", "ollama")
    monkeypatch.delenv("ASSURE_OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("ASSURE_OLLAMA_MODEL_DRAFT", raising=False)
    monkeypatch.delenv("ASSURE_OLLAMA_MODEL_COMPARE", raising=False)
    monkeypatch.delenv("ASSURE_OLLAMA_MODEL_B", raising=False)
    assert orchestrator_module.get_compare_pair() == ("ollama/qwen2.5:1.5b", "ollama/llama3.2:1b")
