"""Production smoke checks after v1.5 deploy (public endpoints only)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

PRODUCTION = (os.environ.get("ASSURE_PRODUCTION_URL") or "https://getassureai.com").rstrip("/")
pytestmark = pytest.mark.skipif(
    os.environ.get("ASSURE_LIVE_SMOKE") != "1",
    reason="Set ASSURE_LIVE_SMOKE=1 to hit production.",
)


def _get(path: str) -> dict:
    req = urllib.request.Request(
        f"{PRODUCTION}{path}",
        method="GET",
        headers={"User-Agent": "AssureDeploySmoke/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


@pytest.mark.integration
def test_production_health_v15():
    data = _get("/health")
    assert data.get("ok") is True
    assert data.get("status") == "healthy"
    ui = data.get("ui") or {}
    css = ui.get("css_version")
    js = ui.get("js_version")
    assert css and js and css.startswith("assure-")
    assert css == js
    sha = str(data.get("build_sha") or "")
    assert len(sha) >= 7


@pytest.mark.integration
def test_production_api_health():
    data = _get("/api/health")
    assert data.get("ok") is True


@pytest.mark.integration
def test_production_limits_multi_page():
    data = _get("/health")
    limits = data.get("limits") or {}
    assert int(limits.get("max_pages") or 0) >= 50
