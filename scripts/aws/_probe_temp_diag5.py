"""Does the provider pin hold when the compile path streams?

litellm.completion(stream=True, extra_body={"provider": {...}}), the real compile
system prompt, temperature=0.0, three times. Hashes must be identical.
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
PIN = {"order": ["Alibaba"], "allow_fallbacks": False}
MSGS = [
    {"role": "system", "content": _COMPILE_SYSTEM},
    {"role": "user", "content": "User intent:\nSummarize the coverage limits."},
]


def sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


def run(label, pin):
    kwargs = dict(
        model=MODEL,
        messages=MSGS,
        max_tokens=700,
        temperature=0.0,
        stream=True,
        stream_options={"include_usage": True},
        **extra,
    )
    if pin:
        kwargs["extra_body"] = {"provider": pin}
    stream = litellm.completion(**kwargs)
    full = ""
    for chunk in stream:
        choice = chunk.choices[0] if chunk.choices else None
        if choice is not None:
            c = getattr(getattr(choice, "delta", None), "content", None)
            if c:
                full += str(c)
    print("  %-18s pin=%-8s sha=%s len=%d" % (label, bool(pin), sha(full)[:16], len(full)))
    return sha(full)


print("== streaming, pinned to Alibaba, temp=0.0 (x3) ==")
p = [run("stream-pin-%d" % i, PIN) for i in (1, 2, 3)]
print("  distinct:", len(set(p)), "/3")

print("\n== streaming, NO pin, temp=0.0 (x3) ==")
u = [run("stream-nopin-%d" % i, None) for i in (1, 2, 3)]
print("  distinct:", len(set(u)), "/3")
