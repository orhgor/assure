"""How stable is a pinned provider really? 8 streaming calls each on the real
compile path, for the deterministic candidates. Reports the hash distribution.
"""
import collections, hashlib, os
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
N = 8


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


for order in (["Alibaba"], ["Novita"]):
    print("\n== pin order=%s, %d streaming runs ==" % (order, N))
    counts = collections.Counter()
    for i in range(N):
        try:
            h, n = stream_once(order)
            counts[(h[:16], n)] += 1
        except Exception as exc:
            counts[("ERROR:" + type(exc).__name__, 0)] += 1
    for (h, n), c in counts.most_common():
        print("  %-18s len=%-5d x%d" % (h, n, c))
    print("  distinct: %d/%d" % (len(counts), N))

print("\n== control: no pin, %d streaming runs ==" % N)
counts = collections.Counter()
for i in range(N):
    kwargs = dict(model=MODEL, messages=MSGS, max_tokens=700, temperature=0.0,
                  stream=True, stream_options={"include_usage": True}, **extra)
    st = litellm.completion(**kwargs)
    txt = ""
    for chunk in st:
        ch = chunk.choices[0] if chunk.choices else None
        if ch is not None and getattr(getattr(ch, "delta", None), "content", None):
            txt += str(ch.delta.content)
    counts[(sha(txt)[:16], len(txt))] += 1
for (h, n), c in counts.most_common():
    print("  %-18s len=%-5d x%d" % (h, n, c))
print("  distinct: %d/%d" % (len(counts), N))
