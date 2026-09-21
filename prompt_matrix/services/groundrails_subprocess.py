"""Subprocess bridge to groundrails (Python 3.12 venv) from the main app."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GROUNDRAILS_TIMEOUT_SEC = 5.0
_USE_GROUNDRAILS_ENV = "USE_GROUNDRAILS_SERVICE"
_GROUNDRAILS_PYTHON_ENV = "GROUNDRAILS_PYTHON"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def groundrails_python() -> str:
    configured = (os.environ.get(_GROUNDRAILS_PYTHON_ENV) or "").strip()
    if configured:
        return configured
    candidate = _repo_root() / ".venv312" / "bin" / "python"
    if candidate.is_file():
        return str(candidate)
    return ""


def groundrails_service_enabled() -> bool:
    flag = (os.environ.get(_USE_GROUNDRAILS_ENV) or "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return False
    py = groundrails_python()
    return bool(py and Path(py).is_file())


def verify_claim_with_groundrails(claim: str, source: str) -> dict[str, Any] | None:
    """
    Invoke prompt_matrix.groundrails_cli in the groundrails venv.
    Returns parsed JSON dict or None when disabled, timed out, or on error.
    """
    if not groundrails_service_enabled():
        return None

    python_bin = groundrails_python()
    payload = json.dumps({"claim": claim, "source": source}, ensure_ascii=False)
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(_repo_root()))

    try:
        proc = subprocess.run(
            [python_bin, "-m", "prompt_matrix.groundrails_cli"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=GROUNDRAILS_TIMEOUT_SEC,
            check=True,
            env=env,
            cwd=str(_repo_root()),
        )
    except subprocess.TimeoutExpired:
        logger.warning("Groundrails subprocess timed out after %ss", GROUNDRAILS_TIMEOUT_SEC)
        return None
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "Groundrails subprocess failed (exit %s): %s",
            exc.returncode,
            (exc.stderr or exc.stdout or "").strip()[:500],
        )
        return None

    stdout = (proc.stdout or "").strip()
    if not stdout:
        return None

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        logger.warning("Groundrails subprocess returned invalid JSON: %s", stdout[:200])
        return None

    if data.get("error"):
        logger.warning("Groundrails CLI error: %s", data.get("error"))
        return None

    return data


def cli_result_to_verdict(claim: str, cli: dict[str, Any]) -> dict[str, Any]:
    """Map CLI JSON to ClaimVerifier verdict shape."""
    grounded = bool(cli.get("grounded"))
    verdict = str(cli.get("verdict") or ("grounded" if grounded else "ungrounded"))
    if verdict == "uncertain":
        grounded = False
    passage = str(cli.get("passage") or "")
    score = float(cli.get("score") or (0.85 if grounded else 0.0))
    return {
        "claim": claim,
        "grounded": grounded,
        "score": score,
        "support": {"passage": passage, "offset": 0} if passage else None,
        "contradiction": cli.get("contradiction"),
        "engine": str(cli.get("engine") or "groundrails-subprocess"),
    }
