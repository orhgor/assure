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
        "image_is_cached",
        "MIN_DISK_GB_FOR_BUILD",
        "--force-recreate",
        "health ok=false",
        "rollback_on_failure",
        "assure-last-deploy.txt",
    ):
        assert needle in text, f"missing hardening marker: {needle}"


def test_rollback_script_present() -> None:
    rollback = ROOT / "scripts" / "aws" / "rollback.sh"
    subprocess.run(["bash", "-n", str(rollback)], check=True)
    text = rollback.read_text(encoding="utf-8")
    assert "PREVIOUS=" in text
    assert "assure-last-deploy.txt" in text
    assert "/health" in text


def test_dockerfile_is_multistage_without_torch() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "AS builder" in text
    assert "AS runtime" in text
    assert "gunicorn" in text
    assert "python:3.11-slim" in text
    assert "torch" not in text.lower()
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".venv" in dockerignore


def test_staging_compose_isolates_data() -> None:
    text = (ROOT / "docker-compose.staging.yml").read_text(encoding="utf-8")
    assert "./data-staging:/app/data" in text
    assert "ASSURE_ENV: staging" in text
    assert "8765" in text


def test_app_docker_workflow_uses_gha_cache() -> None:
    text = (ROOT / ".github" / "workflows" / "app-docker.yml").read_text(encoding="utf-8")
    assert "docker/setup-buildx-action@v3" in text
    assert "cache-from: type=gha" in text
    assert "cache-to: type=gha,mode=max" in text
    assert "${{ github.sha }}" in text


def test_auto_heal_cron_uses_health_endpoint() -> None:
    text = AUTOHEAL.read_text(encoding="utf-8")
    assert "127.0.0.1:8765/health" in text
    assert "rm -f -s assure-app" in text
