"""Static checks for production redeploy hardening."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REDEPLOY = ROOT / "scripts" / "aws" / "redeploy-app.sh"
AUTOHEAL = ROOT / "scripts" / "aws" / "install-auto-heal-cron.sh"


def test_redeploy_app_bash_syntax() -> None:
    subprocess.run(["bash", "-n", str(REDEPLOY)], check=True)


def test_redeploy_app_hardening_markers() -> None:
    text = REDEPLOY.read_text(encoding="utf-8")
    for needle in (
        "acquire_redeploy_lock",
        "remove_stale_app_container",
        "pull_image_with_retry",
        "MIN_DISK_GB_FOR_BUILD",
        "--force-recreate",
        "health ok=false",
    ):
        assert needle in text, f"missing hardening marker: {needle}"


def test_auto_heal_cron_uses_health_endpoint() -> None:
    text = AUTOHEAL.read_text(encoding="utf-8")
    assert "127.0.0.1:8765/health" in text
    assert "rm -f -s assure-app" in text
