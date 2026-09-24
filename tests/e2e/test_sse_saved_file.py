"""SSE coverage captured from a saved file (curl-to-file) instead of
Playwright's response-body cache, which evicts SSE content after navigation.
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid

import pytest

pytestmark = pytest.mark.e2e

API_BASE = "http://localhost:8899"


def test_verified_event_from_saved_file(active_project, tmp_path):
    """Capture the SSE stream to a file via curl, then assert the verified event
    and z3_results are present."""
    out = tmp_path / "sse.sse"
    with open(out, "w") as f:
        subprocess.run(
            [
                "curl",
                "-N",
                "-X",
                "POST",
                f"{API_BASE}/api/projects/shell-proto-54fe89/draft/stream",
                "-H",
                "content-type: application/json",
                "-d",
                json.dumps(
                    {
                        "intent": "summarize the key CPT codes " + uuid.uuid4().hex[:8],
                        "compileType": "full",
                        "substrate_file_ids": ["edge-2a6e72ea5e8540f6"],
                        "target_ai": "openrouter/qwen/qwen3-next-80b-a3b-instruct",
                    }
                ),
            ],
            timeout=300,
            text=True,
            stdout=f,
            stderr=subprocess.DEVNULL,
        )

    content = open(out).read()
    assert "event: verified" in content, "verified event missing"
    m = re.search(r'"z3_results":\s*\{[^}]*\}', content)
    assert m, "z3_results missing from SSE payload"
