"""ASSURE_LLM_BACKEND routes every task's model: bedrock by default, ollama when
set explicitly. OpenRouter and the legacy ``cloud`` policies were removed on
2026-10-01 (user decision); a leftover value of either reads as Bedrock."""

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


def test_leftover_cloud_or_openrouter_reads_as_bedrock(reload_policies, monkeypatch):
    """An old .env with ``cloud`` / ``openrouter`` must not route to a backend
    nothing can serve (OpenRouter removed 2026-10-01)."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    for value in ("cloud", "openrouter", ""):
        mod = reload_policies(value)
        assert mod.llm_backend() == "bedrock", value
        assert mod.TASK_POLICIES[TaskType.DRAFT_COMPILE].litellm_model == "bedrock/us.anthropic.claude-sonnet-5-5"


def test_ollama_backend_routes_every_task(reload_policies, monkeypatch):
    monkeypatch.setenv("ASSURE_OLLAMA_MODEL", "qwen2.5:1.5b")
    mod = reload_policies("ollama")
    models = {p.litellm_model for p in mod.TASK_POLICIES.values()}
    assert models == {"ollama/qwen2.5:1.5b"}
    assert all(p.max_output_tokens <= 4096 for p in mod.TASK_POLICIES.values())
    assert mod.resolve_model("deepseek/deepseek-chat") == "ollama/qwen2.5:1.5b"


def test_bedrock_backend_needs_no_key(reload_policies, monkeypatch):
    for k in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "ASSURE_BEDROCK_MODEL_B"):
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
    # Both Compare columns default to Sonnet 5.5 (2026-10-01): the pair is two
    # samples of one model unless ASSURE_BEDROCK_MODEL_B names another.
    assert a.startswith("bedrock/") and b.startswith("bedrock/")


def test_litellm_kwargs_turns_on_drop_params(monkeypatch):
    import litellm

    monkeypatch.setattr(litellm, "drop_params", False)
    from prompt_matrix.keys import litellm_kwargs_for

    litellm_kwargs_for("bedrock")
    assert litellm.drop_params is True


def test_bedrock_backend_defaults_and_overrides(reload_policies, monkeypatch):
    """User decision 2026-10-01: the document read on Opus 5.5, every other
    prompt on Sonnet 5.5, through the region's inference profile (``us.`` in
    us-east-1). A bare ``anthropic.…`` id gets the prefix; ids that already
    carry one, or an ARN, pass through."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    for k in ("ASSURE_BEDROCK_MODEL", "ASSURE_BEDROCK_MODEL_DRAFT", "ASSURE_BEDROCK_MODEL_ANALYSIS",
              "ASSURE_BEDROCK_MODEL_B", "ASSURE_BEDROCK_MODEL_PARSE"):
        monkeypatch.delenv(k, raising=False)
    mod = reload_policies("bedrock")
    for task, pol in mod.TASK_POLICIES.items():
        assert pol.litellm_model == "bedrock/us.anthropic.claude-sonnet-5-5", task
    assert mod.resolve_model("x", role="analysis") == "bedrock/us.anthropic.claude-sonnet-5-5"
    assert mod.bedrock_model("parse") == "bedrock/us.anthropic.claude-opus-5-5"
    assert mod.bedrock_model("b") == "bedrock/us.anthropic.claude-sonnet-5-5"

    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    assert mod.bedrock_model("draft") == "bedrock/eu.anthropic.claude-sonnet-5-5"

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


def test_ollama_backend_one_model_per_stage(reload_policies, monkeypatch):
    """User's table (2026-09-25): Parsing / Prompt Compile / Red-Hat / Evidence /
    Compare each get their own open model from .env; unset stages fall back to
    ASSURE_OLLAMA_MODEL; the old _B name still means Compare."""
    for k in ("ASSURE_OLLAMA_MODEL", "ASSURE_OLLAMA_MODEL_B", "ASSURE_OLLAMA_MODEL_PARSE", "ASSURE_OLLAMA_MODEL_DRAFT",
              "ASSURE_OLLAMA_MODEL_REDHAT", "ASSURE_OLLAMA_MODEL_EVIDENCE", "ASSURE_OLLAMA_MODEL_COMPARE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ASSURE_OLLAMA_MODEL", "qwen2.5:14b")
    monkeypatch.setenv("ASSURE_OLLAMA_MODEL_PARSE", "qwen2.5:7b")
    monkeypatch.setenv("ASSURE_OLLAMA_MODEL_EVIDENCE", "qwen2.5:7b")
    monkeypatch.setenv("ASSURE_OLLAMA_MODEL_B", "gemma3:27b")
    mod = reload_policies("ollama")
    P = mod.TASK_POLICIES
    assert P[TaskType.FIELD_EXTRACTION].litellm_model == "ollama/qwen2.5:7b"
    assert P[TaskType.SEMANTIC_VALIDATION].litellm_model == "ollama/qwen2.5:7b"
    for t in (TaskType.DRAFT_COMPILE, TaskType.DEEP_SYNTHESIS, TaskType.SUMMARIZE_NODE, TaskType.SURGICAL_EDIT,
              TaskType.REDHAT, TaskType.MACRO_AUDIT):
        assert P[t].litellm_model == "ollama/qwen2.5:14b", t
    assert mod.resolve_model("x", role="analysis") == "ollama/qwen2.5:7b"   # lock inference = evidence
    assert mod.resolve_model("x", role="b") == "ollama/gemma3:27b"
    assert mod.local_models_by_stage() == {"parse": "qwen2.5:7b", "draft": "qwen2.5:14b", "redhat": "qwen2.5:14b",
                                          "evidence": "qwen2.5:7b", "compare": "gemma3:27b"}
    monkeypatch.setenv("ASSURE_OLLAMA_MODEL_REDHAT", "qwen2.5:32b")
    assert mod.local_model("redhat") == "ollama/qwen2.5:32b"


def test_only_an_explicit_ollama_leaves_bedrock(reload_policies, monkeypatch):
    """No key-sniffing any more: before 2026-10-01 an OPENROUTER_API_KEY with the
    switch unset meant OpenRouter and no key meant Ollama. Now unset = Bedrock,
    ``ollama`` = local, and an OpenRouter key changes nothing."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    mod = reload_policies("")
    assert mod.llm_backend() == "bedrock"
    assert not hasattr(mod, "openrouter_model")
    monkeypatch.setenv("ASSURE_LLM_BACKEND", "ollama")
    assert mod.llm_backend() == "ollama"
    monkeypatch.setenv("ASSURE_LLM_BACKEND", "OLLAMA ")
    assert mod.llm_backend() == "ollama"
