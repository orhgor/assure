"""Lock inference follows the app's LLM backend (2026-09-27).

Until then ``_ensure_provider_key`` accepted only ``gemini/`` and
``openrouter/`` ids and raised "OpenRouter API key not configured for lock
inference." on an Ollama or Bedrock box, so every compile there skipped its
locks. OpenRouter was removed on 2026-10-01: the default is Bedrock Sonnet 5.5;
an explicitly passed Gemini model keeps its key check.
"""

from __future__ import annotations

import json

import pytest

from prompt_matrix.services import lock_inference
from prompt_matrix.services.lock_inference import (
    _ensure_provider_key,
    infer_lock_candidates,
    is_hosted_model,
    resolve_lock_inference_model,
)

MEMO = (
    "The policy limit is five million dollars per occurrence and the deductible "
    "is twenty five thousand dollars, subject to a 40 percent coinsurance clause."
)
ANSWER = json.dumps(
    {
        "candidates": [
            {
                "entity": "Policy",
                "metric": "limit",
                "period": "2026",
                "scenario": "Actual",
                "value": 5_000_000,
                "unit": "USD",
                "confidence": 0.9,
            }
        ]
    }
)


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch):
    monkeypatch.setattr(lock_inference, "load_keys", lambda: None)
    monkeypatch.delenv("ASSURE_OLLAMA_MODEL_EVIDENCE", raising=False)
    monkeypatch.delenv("ASSURE_OLLAMA_MODEL", raising=False)


def test_ollama_backend_resolves_a_local_model_and_needs_no_key(monkeypatch) -> None:
    monkeypatch.setenv("ASSURE_LLM_BACKEND", "ollama")
    monkeypatch.setattr(lock_inference, "key_present", lambda _name: False)
    model = resolve_lock_inference_model(has_visual_content=True)
    assert model.startswith("ollama/")
    assert not is_hosted_model(model)
    _ensure_provider_key(model)  # does not raise


def test_bedrock_backend_is_accepted_without_a_provider_key(monkeypatch) -> None:
    monkeypatch.setenv("ASSURE_LLM_BACKEND", "bedrock")
    monkeypatch.setattr(lock_inference, "key_present", lambda _name: False)
    model = resolve_lock_inference_model(has_visual_content=False)
    assert model.startswith(("bedrock/", "anthropic.", "eu.", "us.", "apac.", "global."))
    _ensure_provider_key(model)  # does not raise


def test_ollama_inference_goes_through_the_backend_executor(monkeypatch) -> None:
    monkeypatch.setenv("ASSURE_LLM_BACKEND", "ollama")
    monkeypatch.setattr(lock_inference, "key_present", lambda _name: False)
    seen: dict[str, object] = {}

    def fake_backend(model: str, messages: list[dict]) -> str:
        seen["model"] = model
        seen["messages"] = messages
        return ANSWER

    def fail_call_model(*_a, **_k):
        raise AssertionError("hosted call_model must not be used on the ollama backend")

    monkeypatch.setattr(lock_inference, "_backend_completion", fake_backend)
    monkeypatch.setattr(lock_inference, "call_model", fail_call_model)
    result = infer_lock_candidates(MEMO, has_visual_content=False)
    assert str(seen["model"]).startswith("ollama/")
    assert [c["value"] for c in result.candidates] == [5_000_000]
    assert result.model == seen["model"]


def test_default_backend_is_bedrock_sonnet(monkeypatch) -> None:
    for var in ("ASSURE_LLM_BACKEND", "ASSURE_BEDROCK_MODEL", "ASSURE_BEDROCK_MODEL_ANALYSIS",
                "AWS_DEFAULT_REGION", "AWS_REGION"):
        monkeypatch.delenv(var, raising=False)
    for visual in (False, True):
        assert resolve_lock_inference_model(has_visual_content=visual) == "bedrock/us.anthropic.claude-sonnet-5-5"


def test_bedrock_inference_goes_through_the_backend_executor(monkeypatch) -> None:
    monkeypatch.delenv("ASSURE_LLM_BACKEND", raising=False)
    monkeypatch.setattr(lock_inference, "key_present", lambda _name: False)
    seen: dict[str, object] = {}

    def fake_backend(model: str, messages: list[dict]) -> str:
        seen["model"] = model
        return ANSWER

    def fail_call_model(*_a, **_k):
        raise AssertionError("hosted call_model must not be used on the bedrock backend")

    monkeypatch.setattr(lock_inference, "_backend_completion", fake_backend)
    monkeypatch.setattr(lock_inference, "call_model", fail_call_model)
    result = infer_lock_candidates(MEMO, pdf_bytes=b"%PDF-1.4", has_visual_content=True)
    assert str(seen["model"]).startswith("bedrock/")
    assert [c["value"] for c in result.candidates] == [5_000_000]


def test_explicit_gemini_model_still_requires_its_key(monkeypatch) -> None:
    monkeypatch.setattr(lock_inference, "key_present", lambda _name: False)
    with pytest.raises(RuntimeError, match="Gemini API key not configured"):
        infer_lock_candidates(
            MEMO, pdf_bytes=b"%PDF-1.4", has_visual_content=True, model="gemini/gemini-3.6-flash"
        )
