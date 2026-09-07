"""Groundrails subprocess wrapper and env toggle."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from prompt_matrix.services.groundrails_subprocess import (
    cli_result_to_verdict,
    groundrails_service_enabled,
    verify_claim_with_groundrails,
)
from prompt_matrix.services.verifier import ClaimVerifier, native_heuristic_verify


def test_wrapper_returns_verdict(monkeypatch, tmp_path):
    fake_py = tmp_path / "python"
    fake_py.write_text("#!/bin/sh\nexit 0\n")
    fake_py.chmod(0o755)
    monkeypatch.setenv("USE_GROUNDRAILS_SERVICE", "1")
    monkeypatch.setenv("GROUNDRAILS_PYTHON", str(fake_py))
    cli_out = {
        "verdict": "grounded",
        "grounded": True,
        "score": 0.91,
        "passage": "revenue grew by 15%",
        "engine": "groundrails-subprocess",
    }

    with patch("prompt_matrix.services.groundrails_subprocess.subprocess.run") as run:
        run.return_value = MagicMock(stdout=json.dumps(cli_out), stderr="", returncode=0)
        result = verify_claim_with_groundrails(
            "Revenue grew by 15% in Q3.",
            "During Q3, revenue grew by 15% across sectors.",
        )

    assert result is not None
    assert result["verdict"] == "grounded"
    assert result["passage"] == "revenue grew by 15%"
    run.assert_called_once()
    args = run.call_args
    assert args.kwargs.get("timeout") == 5.0


def test_wrapper_handles_timeout(monkeypatch, tmp_path):
    import subprocess

    fake_py = tmp_path / "python"
    fake_py.write_text("#!/bin/sh\nexit 0\n")
    fake_py.chmod(0o755)
    monkeypatch.setenv("USE_GROUNDRAILS_SERVICE", "1")
    monkeypatch.setenv("GROUNDRAILS_PYTHON", str(fake_py))

    with patch("prompt_matrix.services.groundrails_subprocess.subprocess.run") as run:
        run.side_effect = subprocess.TimeoutExpired(cmd="groundrails", timeout=5.0)
        result = verify_claim_with_groundrails("claim", "source")

    assert result is None


def test_wrapper_falls_back_when_disabled(monkeypatch):
    monkeypatch.delenv("USE_GROUNDRAILS_SERVICE", raising=False)
    assert groundrails_service_enabled() is False
    assert verify_claim_with_groundrails("claim", "source") is None

    verifier = ClaimVerifier()
    out = verifier.verify_claim(
        "The company revenue grew by 15% in Q3.",
        "During Q3, the company revenue grew by 15% across all sectors.",
    )
    assert out["engine"] == "native-heuristic-fallback"
    assert out["grounded"] is True


def test_cli_result_to_verdict_uncertain():
    verdict = cli_result_to_verdict(
        "test claim",
        {"verdict": "uncertain", "grounded": True, "score": 0.3, "passage": ""},
    )
    assert verdict["grounded"] is False
    assert verdict["engine"] == "groundrails-subprocess"


def test_native_heuristic_verify():
    out = native_heuristic_verify(
        "Revenue reached twelve million in Q3",
        "Revenue reached twelve million in Q3 for the fiscal period.",
    )
    assert out["grounded"] is True
    assert out["engine"] == "native-heuristic-fallback"
