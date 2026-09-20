"""Confirm the Alibaba pin with 5 streaming runs on the real compile path, and
check the fallback question: is Novita's text identical to Alibaba's? (If not, a
fallback would silently change the document, so allow_fallbacks must be False.)
"""
import hashlib, os
from dotenv import load_dotenv
for c in (".env.staging", ".env.production", ".env"):
    if os.path.exists(c):
        load_dotenv(c)
        break

import litellm
from prompt_matrix.keys import litellm_kwargs_for, provider_slug_for_litellm
from prompt_matrix.routers.draft import _COMPILE_SYSTEM

MODEL = "openrouter/qwen/qwen3-next-80b-a3b-instruct"
extra = litellm_kwargs_for(provider_slug_for_litellm(MODEL))
MSGS = [
    {"role": "system", "content": _COMPILE_SYSTEM},
    {"role": "user", "content": "User intent:\nSummarize the coverage limits."},
]


def sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


def stream_once(order):
    kwargs = dict(model=MODEL, messages=MSGS, max_tokens=700, temperature=0.0,
                  stream=True, stream_options={"include_usage": True}, **extra)
    kwargs["extra_body"] = {"provider": {"order": order, "allow_fallbacks": False}}
    st = litellm.completion(**kwargs)
    txt = ""
    for chunk in st:
        ch = chunk.choices[0] if chunk.choices else None
        if ch is not None and getattr(getattr(ch, "delta", None), "content", None):
            txt += str(ch.delta.content)
    return sha(txt), len(txt)


print("== Alibaba, 5 runs, streaming, real compile prompt, temp=0.0 ==")
hs = []
for i in range(5):
    h, n = stream_once(["Alibaba"])
    hs.append(h)
    print("  run%d sha=%s len=%d" % (i + 1, h[:16], n))
print("  distinct: %d/5" % len(set(hs)))

print("\n== Novita, 2 runs (fallback candidate) ==")
ns = []
for i in range(2):
    h, n = stream_once(["Novita"])
    ns.append(h)
    print("  run%d sha=%s len=%d" % (i + 1, h[:16], n))
print("  distinct: %d/2" % len(set(ns)))
print("\n  Alibaba text == Novita text? %s" % (set(hs) & set(ns) != set()))
