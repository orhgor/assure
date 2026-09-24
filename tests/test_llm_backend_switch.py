"""ASSURE_LLM_BACKEND routes every task's model: ollama locally, bedrock on an
IAM-only server, the cloud policies otherwise."""

from __future__ import annotations

import importlib

import pytest

from prompt_matrix import cost_governance
from prompt_matrix.cost_governance import TaskType, resolve_model


@pytest.fixture
def reload_policies(monkeypatch):
    def _with(backend: str):
        monkeypatch.setenv("ASSURE_LLM_BACKEND", backend)
        mod = importlib.reload(cost_governance)
        return mod

    yield _with
    monkeypatch.delenv("ASSURE_LLM_BACKEND", raising=False)
    importlib.reload(cost_governance)


def test_cloud_backend_keeps_policies(reload_policies):
    mod = reload_policies("")
    assert mod.TASK_POLICIES[TaskType.DRAFT_COMPILE].litellm_model.startswith("openrouter/")
    assert mod.resolve_model("deepseek/deepseek-chat") == "deepseek/deepseek-chat"


def test_ollama_backend_routes_every_task(reload_policies, monkeypatch):
    monkeypatch.setenv("ASSURE_OLLAMA_MODEL", "qwen2.5:1.5b")
    mod = reload_policies("ollama")
    models = {p.litellm_model for p in mod.TASK_POLICIES.values()}
    assert models == {"ollama/qwen2.5:1.5b"}
    assert all(p.max_output_tokens <= 4096 for p in mod.TASK_POLICIES.values())
    assert mod.resolve_model("deepseek/deepseek-chat") == "ollama/qwen2.5:1.5b"


def test_bedrock_backend_needs_no_key(reload_policies, monkeypatch):
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    mod = reload_policies("bedrock")
    model = mod.TASK_POLICIES[TaskType.DRAFT_COMPILE].litellm_model
    assert model.startswith("bedrock/")
    from prompt_matrix.keys import litellm_kwargs_for, provider_slug_for_litellm

    assert provider_slug_for_litellm(model) == "bedrock"
    assert "api_key" not in litellm_kwargs_for("bedrock")
    assert litellm_kwargs_for("bedrock")["aws_region_name"]
    from prompt_matrix.llm.orchestrator import get_compare_pair

    a, b = get_compare_pair()
    assert a.startswith("bedrock/") and b.startswith("bedrock/") and a != b
