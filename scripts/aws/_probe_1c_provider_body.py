#!/usr/bin/env python3
"""1C — name the upstream, from OpenRouter's own response body.

usage: _probe_1c_provider_body.py [calls]

The generation lookup endpoint 404s for this key (5/5 tried), so the provider is
read where OpenRouter states it: the `provider` field of the response body. Five
calls with the compile's exact routing pin and sampling parameters, non-streaming
because the field is only returned on a complete response. httpx.Client.send is
wrapped to capture the raw body; headers are never printed, so no credential can
leak. Streaming is where the app calls, so this names the provider the pin
selects rather than claiming to be the app's own call — the app's frames carry
its own UUID request_id, not OpenRouter's generation id.
"""
from __future__ import annotations

import json
import os
import sys

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


def main() -> None:
    import httpx
    import litellm

    bodies: list[str] = []
    orig = httpx.Client.send

    def send(self, request, **kwargs):  # noqa: ANN001
        resp = orig(self, request, **kwargs)
        try:
            if "openrouter.ai" in str(request.url):
                bodies.append(resp.read().decode("utf-8", "replace"))
        except Exception:
            pass
        return resp

    print(f"model={MODEL} pin={PIN} key_present={bool(KEY)} calls={CALLS}")
    httpx.Client.send = send
    try:
        for i in range(1, CALLS + 1):
            before = len(bodies)
            resp = litellm.completion(
                model=MODEL,
                messages=[{"role": "user", "content": "Reply with the single word: pinned."}],
                max_tokens=16,
                temperature=0.0,
                top_p=1.0,
                seed=0,
                extra_body={"provider": dict(PIN)},
                **({"api_key": KEY} if KEY else {}),
            )
            body = bodies[-1] if len(bodies) > before else ""
            if not body:
                print(f"call {i}: no body captured")
                continue
            d = json.loads(body)
            print(f"call {i}: id={d.get('id')} provider={d.get('provider')!r} "
                  f"model={d.get('model')} text={(d.get('choices') or [{}])[0].get('message', {}).get('content', '')!r}")
    finally:
        httpx.Client.send = orig


if __name__ == "__main__":
    main()
