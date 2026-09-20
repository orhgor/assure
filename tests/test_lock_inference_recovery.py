"""Lock inference: a truncated answer, and the budget it is written under.

``services/lock_inference`` asks a model for one line of compact JSON because a
figures-dense memo needs ~30 candidates; the answer is cut off at the output
budget, and the draft's figures then go unchecked. Two things fix that, and both
fail silently:

* a call that passes no ``max_tokens`` of its own — the runner asks for the
  intent's budget (2048 for deepseek-chat), where a hardcoded 1024 cut a renewal
  memo's extraction off mid-object;
* ``_recover_complete_candidates``, which reads the complete candidate objects out
  of an answer whose braces never balance. ``_extract_json_block`` needs the braces
  to balance, so a truncated answer yielded nothing and the draft read as having no
  figures at all.

The acceptance rules (confidence floor, a numeric value, a named metric) apply to
recovered candidates exactly as they apply to a clean answer: recovery is not a
bypass.
"""

from __future__ import annotations

import json
import os

import pytest

from prompt_matrix.services import lock_inference
from prompt_matrix.services.lock_inference import (
    _parse_model_json,
    _recover_complete_candidates,
    infer_lock_candidates,
)

MEMO_TEXT = (
    "The policy limit is five million dollars per occurrence and the deductible is "
    "twenty five thousand dollars, subject to a forty percent coinsurance clause."
)

CANDIDATE = {
    "entity": "TRIA",
    "metric": "cap",
    "period": "2026",
    "scenario": "Actual",
    "value": 100_000_000_000,
    "unit": "USD",
    "confidence": 0.9,
}


def _answer(*candidates: dict) -> str:
    return json.dumps({"candidates": list(candidates)})


def test_a_complete_answer_is_read_as_a_document() -> None:
    parsed = _parse_model_json(_answer(CANDIDATE))
    assert [c["value"] for c in parsed] == [CANDIDATE["value"]]


def _truncated_mid_object(*candidates: dict) -> str:
    """A complete-answer prefix with one more object started and never closed."""
    return _answer(*candidates)[:-2] + ', {"entity": "TRIA", "metric": "deductib'


def test_a_truncated_answer_yields_the_candidates_it_completed() -> None:
    """The answer is cut mid-object; the objects before the cut are still extractions.

    Measured on the renewal memo: 6,709 characters cut at the output limit and zero
    candidates parsed, because ``_extract_json_block`` finds no balanced block. The
    draft's figures then went unchecked with no reason recorded.
    """
    other = {**CANDIDATE, "metric": "deductible", "value": 25_000, "period": "2027"}
    truncated = _truncated_mid_object(CANDIDATE, other)

    recovered = _recover_complete_candidates(truncated)
    assert [c["metric"] for c in recovered] == ["cap", "deductible"]
    assert [c["value"] for c in recovered] == [100_000_000_000, 25_000]
    # …and the same answer comes back through the parser the model's reply takes.
    assert _parse_model_json(truncated) == recovered


def test_nothing_is_inferred_from_the_truncated_object_itself() -> None:
    """The half-written object is dropped, not completed by guesswork."""
    recovered = _recover_complete_candidates(_truncated_mid_object(CANDIDATE))
    assert [c["metric"] for c in recovered] == ["cap"]


def test_recovered_candidates_pass_the_same_acceptance_rules() -> None:
    """A recovered candidate below the confidence floor is still refused.

    Recovery reads the objects a truncation left behind; it is not a way past the
    rules a clean answer is held to.
    """
    weak = {**CANDIDATE, "metric": "weak", "confidence": 0.2}
    recovered = _recover_complete_candidates(_truncated_mid_object(CANDIDATE, weak))
    assert [c["metric"] for c in recovered] == ["cap"]


def test_an_answer_with_no_candidates_yields_nothing() -> None:
    assert _recover_complete_candidates('{"candidates": [') == []
    assert _parse_model_json(_answer()) == []


def test_the_call_asks_for_the_intent_budget_rather_than_its_own_cap(monkeypatch) -> None:
    """No ``max_tokens`` of its own: the runner resolves the intent's budget.

    The 1024 that used to sit here was under the cap and cut a figures-dense memo's
    extraction off mid-object — a truncated answer is not a partial ledger, it is no
    ledger, and the draft's figures went unchecked. A hardcoded cap returning here
    is the defect coming back, so the contract asserted is the one the fix states.
    """
    captured: dict = {}

    def fake_call_model(model, messages, **kwargs):
        captured["model"] = model
        captured["kwargs"] = kwargs
        return _answer(CANDIDATE)

    monkeypatch.setattr(lock_inference, "call_model", fake_call_model)
    monkeypatch.setattr(lock_inference, "load_keys", lambda: None)
    monkeypatch.setattr(lock_inference, "key_present", lambda _name: True)

    result = infer_lock_candidates(MEMO_TEXT)
    assert captured["kwargs"]["intent"] == "analysis"
    assert "max_tokens" not in captured["kwargs"]
    assert [c["metric"] for c in result.candidates] == ["cap"]


def test_a_text_too_short_to_hold_a_figure_costs_no_call(monkeypatch) -> None:
    """The guard in front of the model: nothing to extract, nothing spent."""

    def fail(*_a, **_k):
        raise AssertionError("no model call for text below the minimum")

    monkeypatch.setattr(lock_inference, "call_model", fail)
    result = infer_lock_candidates("short")
    assert result.candidates == []


def test_a_no_figure_answer_reports_no_locks(monkeypatch) -> None:
    """A memo the model finds no figures in: zero locks, and the gate says SKIPPED.

    ``verify_locks`` (the gate) reports SKIPPED with ``metrics_checked == 0`` — the
    other half of this behaviour lives in tests/test_draft.py, which asserts the
    status rather than the extraction.
    """
    monkeypatch.setattr(lock_inference, "call_model", lambda *_a, **_k: _answer())
    monkeypatch.setattr(lock_inference, "load_keys", lambda: None)
    monkeypatch.setattr(lock_inference, "key_present", lambda _name: True)
    result = infer_lock_candidates(MEMO_TEXT)
    assert result.candidates == []
    assert result.model == "deepseek/deepseek-chat"


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Z3 intermittently segfaults on GitHub Actions Python 3.11",
)
def test_a_no_figure_draft_is_skipped_by_the_gate_not_passed() -> None:
    from prompt_matrix.routers.draft import verify_locks

    result = verify_locks([], "The memo discusses coverage and exclusions at length.")
    assert result["status"] == "SKIPPED"
    assert result["metrics_checked"] == 0
    assert result.get("skip_reason")
