"""Compile-system vs preview consistency regression tests.

- /api/compile-system  must return EXACTLY the compile path's system message.
- /api/preview (Compose app) must still return the per-task prompt, NOT the
  static compile system message.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

pytestmark = pytest.mark.e2e

API_BASE = "http://localhost:8899"


def _post(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(req).read())


def test_compile_system_matches_compile_path(active_project):
    """The new endpoint returns exactly what the compile path sends as its
    system message."""
    body = _post(f"{API_BASE}/api/compile-system", {})
    from prompt_matrix.routers.draft import _COMPILE_SYSTEM

    assert (
        body["prompt"] == _COMPILE_SYSTEM
    ), "/api/compile-system diverged from the compile path's system message"


def test_preview_default_is_per_task(active_project):
    """Compose's /api/preview still returns the per-task prompt, not the
    static compile system message."""
    body = _post(
        f"{API_BASE}/api/preview",
        {"task": "smoke", "target_ai": "claude"},
    )
    from prompt_matrix.routers.draft import _COMPILE_SYSTEM

    assert body["prompt"] != _COMPILE_SYSTEM, (
        "/api/preview returned the static compile system message — "
        "Compose's per-task preview is broken"
    )
    assert "smoke" in body["prompt"].lower(), "/api/preview did not include the ask in the prompt"
