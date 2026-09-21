"""Capture the exact body litellm sends for a pinned call, streaming vs not.

Monkeypatches httpx.Client.send so the outgoing JSON is on the record.
"""
import hashlib, json, os
from dotenv import load_dotenv
for c in (".env.staging", ".env.production", ".env"):
    if os.path.exists(c):
        load_dotenv(c)
        break

import httpx

CAPTURED = []
_orig_send = httpx.Client.send


def _patched(self, request, **kw):
    try:
        if "/chat/completions" in str(request.url):
            content = request.content
            text = content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content)
            CAPTURED.append(text)
            print("  OUTGOING BODY: %s" % text[:900])
    except Exception as exc:  # pragma: no cover
        print("  capture err:", exc)
    return _orig_send(self, request, **kw)


httpx.Client.send = _patched

import litellm
from prompt_matrix.keys import litellm_kwargs_for, provider_slug_for_litellm
from prompt_matrix.routers.draft import _COMPILE_SYSTEM

MODEL = "openrouter/qwen/qwen3-next-80b-a3b-instruct"
extra = litellm_kwargs_for(provider_slug_for_litellm(MODEL))
PIN = {"order": ["Alibaba"], "allow_fallbacks": False}
MSGS = [
    {"role": "system", "content": _COMPILE_SYSTEM},
    {"role": "user", "content": "User intent:\nSummarize the coverage limits."},
]


def sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


print("== A. NON-streaming, extra_body pin ==")
CAPTURED.clear()
r = litellm.completion(model=MODEL, messages=MSGS, max_tokens=700, temperature=0.0,
                       extra_body={"provider": PIN}, **extra)
print("  sha=%s len=%d" % (sha(r.choices[0].message.content or "")[:16],
                           len(r.choices[0].message.content or "")))
print("  captured %d body(s); 'provider' present: %s"
      % (len(CAPTURED), ["provider" in c for c in CAPTURED]))

print("\n== B. STREAMING, extra_body pin ==")
CAPTURED.clear()
kwargs = dict(model=MODEL, messages=MSGS, max_tokens=700, temperature=0.0,
              stream=True, stream_options={"include_usage": True}, **extra)
kwargs["extra_body"] = {"provider": PIN}
st = litellm.completion(**kwargs)
txt = ""
for chunk in st:
    ch = chunk.choices[0] if chunk.choices else None
    if ch is not None and getattr(getattr(ch, "delta", None), "content", None):
        txt += str(ch.delta.content)
print("  sha=%s len=%d" % (sha(txt)[:16], len(txt)))
print("  captured %d body(s); 'provider' present: %s"
      % (len(CAPTURED), ["provider" in c for c in CAPTURED]))
