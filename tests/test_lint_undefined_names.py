"""Every name the package uses is defined or imported.

Why this test exists: ``routers/jdf_memory_routes.py`` called ``ocr_engine()``
without importing it, so the legacy ``/jdf/ingest`` route raised NameError on
every scanned or photographed upload and answered 422 "Document processing
failed" (customer's review.jpeg, found 2026-09-28; ruff F821 had flagged it and
nothing ran ruff). This runs that one check over the package.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_no_undefined_names() -> None:
    ruff = shutil.which("ruff") or str(Path(sys.executable).with_name("ruff"))
    if not Path(ruff).exists():
        pytest.skip("ruff is not installed")
    proc = subprocess.run([ruff, "check", "--select", "F821", "--no-cache", str(ROOT / "prompt_matrix")],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
