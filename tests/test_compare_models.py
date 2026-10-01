"""Compare pair — parallel dispatch and error payload safety."""

from __future__ import annotations

import time
from prompt_matrix.services.compare_models import _model_slot, run_compare_pair


def test_model_slot_does_not_copy_error_into_text() -> None:
    slot = _model_slot(
        {
            "name": "Claude Sonnet 5.5 (Bedrock)",
            "text": "",
            "error": "ERROR: Run aborted due to timeout (60s).",
        },
        "bedrock/us.anthropic.claude-sonnet-5-5",
    )
    assert slot["error"]
    assert slot["text"] == ""


def test_run_compare_pair_parallel_not_sequential(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_single(model: str, intent: str, source_ids=None) -> dict:
        calls.append(model)
        time.sleep(0.15 if model.startswith("bedrock/us.anthropic.claude-opus") else 0.05)
        return {
            "model": model,
            "name": model,
            "text": f"output-{model}",
            "jdf": {"text": f"output-{model}", "divergences": []},
        }

    monkeypatch.setattr(
        "prompt_matrix.services.compare_models.get_compare_pair",
        lambda _idx=0: ("bedrock/us.anthropic.claude-opus-5-5", "bedrock/us.anthropic.claude-sonnet-5-5"),
    )
    monkeypatch.setattr(
        "prompt_matrix.services.compare_models.run_single_model",
        _fake_single,
    )

    started = time.monotonic()
    result = run_compare_pair("parallel check")
    elapsed = time.monotonic() - started

    assert len(calls) == 2
    assert result["models"]["claude"]["text"].startswith("output-")
    assert result["models"]["secondary"]["text"].startswith("output-")
    # Sequential would be ~0.20s+; parallel should finish closer to ~0.15s.
    assert elapsed < 0.25


def test_run_compare_pair_failed_model_has_empty_text(monkeypatch) -> None:
    def _fake_single(model: str, intent: str, source_ids=None) -> dict:
        if model.endswith("claude-sonnet-5-5"):
            raise RuntimeError("ERROR: Run aborted due to timeout (60s).")
        return {
            "model": model,
            "name": "Claude",
            "text": "Boston liability limit is $5M.",
            "jdf": {"text": "Boston liability limit is $5M.", "divergences": []},
        }

    monkeypatch.setattr(
        "prompt_matrix.services.compare_models.get_compare_pair",
        lambda _idx=0: ("bedrock/us.anthropic.claude-opus-5-5", "bedrock/us.anthropic.claude-sonnet-5-5"),
    )
    monkeypatch.setattr(
        "prompt_matrix.services.compare_models.run_single_model",
        _fake_single,
    )

    result = run_compare_pair("Boston liability")
    secondary = result["models"]["secondary"]
    assert secondary["error"]
    assert secondary["text"] == ""
