"""Hallucination minimizer retry logic tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from celery.exceptions import MaxRetriesExceededError

from prompt_matrix.tasks import compile_tasks
from prompt_matrix.verification.grounding import GroundingVerdict, verify_claims


def test_verify_claims_fallback_when_groundrails_missing():
    verdict = verify_claims(
        "Revenue reached twelve million", "Revenue reached twelve million in Q3"
    )
    assert verdict.grounded is True


def test_safe_compile_retries_on_ungrounded_claims():
    with (
        patch.object(compile_tasks, "fetch_vault_markdown", return_value="Source policy text."),
        patch.object(compile_tasks, "generate_draft", return_value="Invented $999M revenue."),
        patch(
            "prompt_matrix.verification.grounding.verify_claims",
            return_value=GroundingVerdict(grounded=False, unverified=["999M"]),
        ),
        patch.object(
            compile_tasks.safe_compile_and_verify,
            "retry",
            side_effect=MaxRetriesExceededError(),
        ),
    ):
        with pytest.raises(MaxRetriesExceededError):
            compile_tasks.safe_compile_and_verify.run("proj-1", "node-1")


def test_safe_compile_commits_when_grounded():
    with (
        patch.object(compile_tasks, "fetch_vault_markdown", return_value="Revenue $12M in Q3."),
        patch.object(compile_tasks, "generate_draft", return_value="Revenue $12M in Q3."),
        patch(
            "prompt_matrix.verification.grounding.verify_claims",
            return_value=GroundingVerdict(grounded=True, unverified=[]),
        ),
        patch(
            "prompt_matrix.llm.orchestrator.orchestrate_node_compilation_sync",
            return_value="OK",
        ),
        patch.object(compile_tasks, "commit_to_ast", return_value={"ok": True, "version": 2}),
        patch.object(compile_tasks.check_and_trigger_automations, "delay") as mock_auto,
    ):
        out = compile_tasks.safe_compile_and_verify.run("proj-1", "node-1")

    assert out["status"] == "success"
    mock_auto.assert_called_once_with("proj-1")
