#!/usr/bin/env python3
"""1C — what served each call, and what was actually on the wire.

usage: _probe_1c_wire.py [project_id] [source_id] [runs]

Two readings, both about the same question: is the compile's provider pin a
claim or a fact?

  A. The app's own path. Posts `runs` compiles, takes the `request_id` the app
     puts on its frames, and asks OpenRouter which upstream served that
     generation (`/api/v1/generation?id=`). Per call: provider name + model.
  B. The wire body. One standalone litellm streaming call with the same model,
     the same pin and the same sampling parameters, with httpx.Client.send
     wrapped so the outgoing JSON is printed (headers are never printed, so the
     API key cannot leak). This is what shows whether `temperature`, `top_p`,
     `seed` and `provider` are really in the request body litellm sends.

Neither reading prints a credential.
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
RUNS = int(sys.argv[3]) if len(sys.argv) > 3 else 3
MODEL = os.environ.get("PROBE_MODEL", "openrouter/qwen/qwen3-next-80b-a3b-instruct")
PIN = {"order": ["Alibaba"], "allow_fallbacks": False}
INTENT = os.environ.get(
    "PROBE_INTENT", "Summarize the exclusions in the Causes of Loss - Special Form."
)


def gate_key() -> str:
    if os.environ.get("SHELL_KEY"):
        return os.environ["SHELL_KEY"]
    raw = Path("/etc/assure/shell-access.env").read_text()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""


def openrouter_key() -> str:
    try:
        from prompt_matrix.keys import litellm_kwargs_for
    except ImportError:
        from keys import litellm_kwargs_for

    return (litellm_kwargs_for("openrouter") or {}).get("api_key") or ""


KEY = openrouter_key()


def generation(rid: str) -> dict:
    req = urllib.request.Request(
        f"https://openrouter.ai/api/v1/generation?id={rid}",
        headers={"Authorization": f"Bearer {KEY}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())["data"]


def app_compile() -> tuple[str, str]:
    req = urllib.request.Request(
        f"{BASE}/api/projects/{PROJECT}/draft/stream",
        data=json.dumps(
            {"intent": INTENT, "compileType": "full", "substrate_file_ids": [SOURCE_ID]}
        ).encode(),
        headers={"Content-Type": "application/json", "X-Shell-Key": gate_key(),
                 "User-Agent": "curl/8.7.1", "Accept": "text/event-stream"},
        method="POST",
    )
    rid = mid = ""
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
    return rid, mid


def read_app() -> None:
    print(f"=== A. the app's own compiles (project={PROJECT}) ===")
    for i in range(1, RUNS + 1):
        rid, mid = app_compile()
        line = f"run {i}: request_id={rid or '(none)'} model_id={mid or '(none)'}"
        if rid:
            try:
                d = generation(rid)
                line += (f"  -> provider_name={d.get('provider_name')}"
                         f" model={d.get('model')}"
                         f" tokens={d.get('tokens_prompt')}->{d.get('tokens_completion')}")
            except Exception as exc:
                line += f"  (generation lookup failed: {type(exc).__name__})"
        print(line)


def read_wire() -> None:
    print("\n=== B. the wire body (standalone call, same params) ===")
    import httpx
    import litellm

    seen: list[str] = []
    orig = httpx.Client.send

    def send(self, request, **kwargs):  # noqa: ANN001
        try:
            if "openrouter.ai" in str(request.url):
                seen.append(request.content.decode("utf-8", "replace"))
        except Exception:
            pass
        return orig(self, request, **kwargs)

    httpx.Client.send = send
    gen_id = ""
    try:
        stream = litellm.completion(
            model=MODEL,
            messages=[{"role": "user", "content": "Reply with the single word: pinned."}],
            max_tokens=16,
            temperature=0.0,
            top_p=1.0,
            seed=0,
            stream=True,
            stream_options={"include_usage": True},
            extra_body={"provider": dict(PIN)},
            **({"api_key": KEY} if KEY else {}),
        )
        for chunk in stream:
            cid = getattr(chunk, "id", None)
            if cid and not gen_id:
                gen_id = str(cid)
    finally:
        httpx.Client.send = orig

    if not seen:
        print("no outgoing openrouter request captured")
        return
    body = json.loads(seen[-1])
    keep = ("model", "temperature", "top_p", "seed", "provider", "stream", "max_tokens")
    print("request body (credential-bearing fields are not in this body):")
    print(json.dumps({k: body.get(k) for k in keep if k in body}, indent=2))
    print("fields present:", sorted(body))
    if gen_id:
        try:
            d = generation(gen_id)
            print(f"this call: provider_name={d.get('provider_name')} model={d.get('model')}")
        except Exception as exc:
            print("generation lookup failed:", type(exc).__name__)


def main() -> None:
    print(f"key_present={bool(KEY)} model={MODEL} pin={PIN}")
    read_app()
    read_wire()


if __name__ == "__main__":
    main()
