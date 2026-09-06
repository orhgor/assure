"""JDF CLI validation smoke test."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def test_jdf_cli_validate_sample_report():
    cli = shutil.which("npx")
    if cli is None:
        return
    report = Path(__file__).resolve().parents[1] / "docs" / "report.jdf"
    assert report.is_file()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["$jdf"]
    assert isinstance(payload.get("pages"), list)

    proc = subprocess.run(
        ["npx", "--yes", "@uurtech/jdf-cli", "validate", str(report)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
