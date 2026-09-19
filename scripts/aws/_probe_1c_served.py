#!/usr/bin/env python3
"""1C — name the upstream that serves the pinned call.

usage: _probe_1c_served.py [calls]

Five streaming calls with the compile's exact parameters (temperature=0, top_p=1,
seed=0, provider={order:[Alibaba], allow_fallbacks:false}), each followed by the
OpenRouter generation lookup, so every call is named by OpenRouter rather than
inferred. Prints the generation id, the provider name, the served model and the
HTTP status of the lookup. No credential is printed.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CALLS = int(sys.argv[1]) if len(sys.argv) > 1 else 5
MODEL = os.environ.get("PROBE_MODEL", "openrouter/qwen/qwen3-next-80b-a3b-instruct")
PIN = {"order": ["Alibaba"], "allow_fallbacks": False}


def openrouter_key() -> str:
    try:
        from prompt_matrix.keys import litellm_kwargs_for
    except ImportError:
        from keys import litellm_kwargs_for

    return (litellm_kwargs_for("openrouter") or {}).get("api_key") or ""


KEY = openrouter_key()


def lookup(gid: str) -> str:
    req = urllib.request.Request(
        f"https://openrouter.ai/api/v1/generation?id={gid}",
        headers={"Authorization": f"Bearer {KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            d = json.loads(resp.read().decode())["data"]
        return (f"provider_name={d.get('provider_name')} model={d.get('model')} "
                f"upstream={d.get('upstream_inference_provider')} "
                f"tokens={d.get('tokens_prompt')}->{d.get('tokens_completion')}")
    except urllib.error.HTTPError as exc:
        return f"lookup HTTP {exc.code}: {exc.read().decode()[:160]}"
    except Exception as exc:
        return f"lookup failed: {type(exc).__name__}: {exc}"


def main() -> None:
    import litellm

    print(f"model={MODEL} pin={PIN} key_present={bool(KEY)}")
    for i in range(1, CALLS + 1):
        ids: list[str] = []
        out = ""
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
            if cid and cid not in ids:
                ids.append(str(cid))
            ch = getattr(chunk, "choices", None)
            if ch:
                d = getattr(ch[0], "delta", None)
                c = getattr(d, "content", None)
                if c:
                    out += c
        gid = next((x for x in ids if x.startswith("gen-")), ids[0] if ids else "")
        print(f"\ncall {i}: text={out.strip()[:40]!r} chunk_ids={ids[:3]}")
        if not gid:
            print("  no generation id on the chunks")
            continue
        print(f"  gen_id={gid}")
        time.sleep(4)
        print("  served:", lookup(gid))


if __name__ == "__main__":
    main()
