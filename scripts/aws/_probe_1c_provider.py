#!/usr/bin/env python3
"""1C — which upstream provider served a compile.

usage: _probe_1c_provider.py [project_id] [source_id]

Posts one compile, reads the `request_id` the app puts on its frames, and asks
OpenRouter what served that generation. The key is read from the app's own keys
module and is never printed: only the provider name and the model id come back.

The point: a routing pin (`provider.order` + `allow_fallbacks=false`) is a claim
about which upstream runs the call. This turns the claim into a reading, per call,
so "the same provider served every call" is evidence rather than an assumption.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")
PROJECT = sys.argv[1] if len(sys.argv) > 1 else "1c-det-copy-0f68e5-1eff24"
SOURCE_ID = sys.argv[2] if len(sys.argv) > 2 else "sub-5aafb9d2546c4c22"
INTENT = os.environ.get(
    "PROBE_INTENT",
    "Summarize the exclusions in the Causes of Loss - Special Form.",
)
RUNS = int(os.environ.get("PROBE_RUNS", "1"))


def gate_key() -> str:
    if os.environ.get("SHELL_KEY"):
        return os.environ["SHELL_KEY"]
    raw = Path("/etc/assure/shell-access.env").read_text()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""


def openrouter_key() -> str:
    try:
        from prompt_matrix.keys import litellm_kwargs_for  # the app's own resolver
    except ImportError:
        from keys import litellm_kwargs_for

    return (litellm_kwargs_for("openrouter") or {}).get("api_key") or ""


def compile_once() -> tuple[str, str, str]:
    req = urllib.request.Request(
        f"{BASE}/api/projects/{PROJECT}/draft/stream",
        data=json.dumps(
            {"intent": INTENT, "compileType": "full", "substrate_file_ids": [SOURCE_ID]}
        ).encode(),
        headers={"Content-Type": "application/json", "X-Shell-Key": gate_key(),
                 "User-Agent": "curl/8.7.1", "Accept": "text/event-stream"},
        method="POST",
    )
    rid = mid = model = ""
    with urllib.request.urlopen(req, timeout=900) as resp:
        for line in resp:
            text = line.decode("utf-8", "replace").rstrip("\n")
            if not text.startswith("data: "):
                continue
            try:
                f = json.loads(text[6:])
            except Exception:
                continue
            if f.get("request_id") and not rid:
                rid = str(f["request_id"])
            if f.get("model_id") and not mid:
                mid = str(f["model_id"])
            if f.get("model") and not model:
                model = str(f["model"])
    return rid, mid, model


def generation(rid: str, key: str) -> dict:
    req = urllib.request.Request(
        f"https://openrouter.ai/api/v1/generation?id={rid}",
        headers={"Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    key = openrouter_key()
    print(f"project={PROJECT} source={SOURCE_ID} runs={RUNS} key_present={bool(key)}")
    for i in range(1, RUNS + 1):
        rid, mid, model = compile_once()
        print(f"\nrun {i}: request_id={rid or '(none)'}  model_id={mid or '(none)'}  model={model or '(none)'}")
        if not rid:
            print("  no generation id on the frames — cannot name the upstream")
            continue
        try:
            data = generation(rid, key)["data"]
        except Exception as exc:
            print("  generation lookup failed:", type(exc).__name__, exc)
            continue
        print("  provider_name:", data.get("provider_name"))
        print("  upstream:", data.get("upstream_inference_provider") or data.get("upstream"))
        print("  served model:", data.get("model"))
        print("  tokens:", data.get("tokens_prompt"), "→", data.get("tokens_completion"))


if __name__ == "__main__":
    main()
