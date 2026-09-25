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


def test_litellm_kwargs_turns_on_drop_params(monkeypatch):
    import litellm

    monkeypatch.setattr(litellm, "drop_params", False)
    from prompt_matrix.keys import litellm_kwargs_for

    litellm_kwargs_for("bedrock")
    assert litellm.drop_params is True


def test_bedrock_backend_splits_drafting_and_analysis(reload_policies, monkeypatch):
    """User decision 2026-09-25: drafting on Sonnet, analysis on Opus, both set
    from .env. A bare ``anthropic.…`` id gets the region's inference-profile
    prefix; ids that already carry one, or an ARN, pass through."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    for k in ("ASSURE_BEDROCK_MODEL", "ASSURE_BEDROCK_MODEL_DRAFT", "ASSURE_BEDROCK_MODEL_ANALYSIS", "ASSURE_BEDROCK_MODEL_B"):
        monkeypatch.delenv(k, raising=False)
    mod = reload_policies("bedrock")
    drafting = {TaskType.DRAFT_COMPILE, TaskType.DEEP_SYNTHESIS, TaskType.SUMMARIZE_NODE, TaskType.SURGICAL_EDIT}
    for task, pol in mod.TASK_POLICIES.items():
        expected = "bedrock/eu.anthropic.claude-sonnet-5" if task in drafting else "bedrock/eu.anthropic.claude-opus-5"
        assert pol.litellm_model == expected, task
    assert mod.resolve_model("x", role="analysis") == "bedrock/eu.anthropic.claude-opus-5"
    assert mod.resolve_model("x") == "bedrock/eu.anthropic.claude-sonnet-5"

    monkeypatch.setenv("ASSURE_BEDROCK_MODEL_DRAFT", "anthropic.claude-sonnet-4-6")
    monkeypatch.setenv("ASSURE_BEDROCK_MODEL_ANALYSIS", "us.anthropic.claude-opus-5-5")
    monkeypatch.setenv("ASSURE_BEDROCK_MODEL_B", "arn:aws:bedrock:eu-central-1:1:inference-profile/p")
    assert mod.bedrock_model("draft") == "bedrock/eu.anthropic.claude-sonnet-4-6"
    assert mod.bedrock_model("analysis") == "bedrock/us.anthropic.claude-opus-5-5"
    assert mod.bedrock_model("b") == "bedrock/arn:aws:bedrock:eu-central-1:1:inference-profile/p"

    # the old single variable still sets both roles
    for k in ("ASSURE_BEDROCK_MODEL_DRAFT", "ASSURE_BEDROCK_MODEL_ANALYSIS"):
        monkeypatch.delenv(k)
    monkeypatch.setenv("ASSURE_BEDROCK_MODEL", "anthropic.claude-opus-4-8")
    assert mod.bedrock_model("draft") == mod.bedrock_model("analysis") == "bedrock/eu.anthropic.claude-opus-4-8"
