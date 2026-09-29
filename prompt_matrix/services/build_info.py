"""Which build produced an artifact.

Why (plan V5 review protocol V1, 2026-09-29): a post-V4 review compared an
export produced by an older deployment against the new code and read five of
six findings as "still broken". Every Parsure report now carries
``execution.build = {"commit", "branch", "source"}`` so a reviewer can tell an
artifact of HEAD from one of last week before judging it. The commit is what
the deploy exported (``ASSURE_BUILD_SHA``, set by the Docker build from
``BUILD_SHA``) or, on a checkout, ``git rev-parse HEAD``; ``"unknown"`` when
neither is known — never a guess. Cached per process: ``git`` is not cheap and
the value cannot change while the process runs.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

_cache: dict[str, Any] | None = None


def _git(args: list[str], cwd: Path) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=2.0)
    except Exception:  # noqa: BLE001 — no git, no repo, no time
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def build_stamp() -> dict[str, Any]:
    """``{"commit": "<short sha>"|"unknown", "branch": "<name>"|None, "source": "env"|"git"|"none"}``."""
    global _cache
    if _cache is not None:
        return dict(_cache)
    sha = (os.environ.get("ASSURE_BUILD_SHA") or os.environ.get("BUILD_SHA") or "").strip()
    branch = (os.environ.get("ASSURE_BUILD_BRANCH") or "").strip() or None
    if branch and branch.lower() in ("unknown", "local"):
        branch = None  # the Dockerfile's ARG default, not a branch
    source = "env" if sha and sha.lower() not in ("local", "unknown") else "none"
    if source == "none":
        root = Path(__file__).resolve().parents[2]
        git_sha = _git(["rev-parse", "HEAD"], root)
        if git_sha:
            sha, source = git_sha, "git"
            branch = branch or (_git(["rev-parse", "--abbrev-ref", "HEAD"], root) or None)
    _cache = {"commit": sha[:12] if sha and source != "none" else "unknown", "branch": branch, "source": source}
    return dict(_cache)


def reset_cache() -> None:
    global _cache
    _cache = None
