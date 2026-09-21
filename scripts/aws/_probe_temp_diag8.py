"""Which upstream provider for qwen3-next-80b is byte-stable through litellm's
STREAMING path (the path the compile actually uses)?

1. list the model's OpenRouter endpoints (provider names)
2. pin each candidate and run 3 streaming litellm calls with the real compile
   system prompt at temperature=0.0
3. print the distinct-hash count per provider
"""
import hashlib, json, os
from dotenv import load_dotenv
for c in (".env.staging", ".env.production", ".env"):
    if os.path.exists(c):
        load_dotenv(c)
        break

import requests
import litellm
from prompt_matrix.keys import litellm_kwargs_for, provider_slug_for_litellm
from prompt_matrix.routers.draft import _COMPILE_SYSTEM

MODEL = "openrouter/qwen/qwen3-next-80b-a3b-instruct"
SLUG = MODEL.split("/", 1)[1]
extra = litellm_kwargs_for(provider_slug_for_litellm(MODEL))
OR_KEY = os.environ.get("OPENROUTER_API_KEY") or ""
BASE = (extra.get("api_base") or "https://openrouter.ai/api/v1").rstrip("/")

print("== endpoints for %s ==" % SLUG)
try:
    r = requests.get(
        "%s/models/%s/endpoints" % (BASE, SLUG),
        headers={"Authorization": "Bearer " + OR_KEY},
        timeout=60,
    )
    j = r.json()
    data = j.get("data") or {}
    names = [e.get("provider_name") or e.get("name") for e in (data.get("endpoints") or [])]
    print("  HTTP=%d providers=%s" % (r.status_code, names))
except Exception as exc:
    names = []
    print("  endpoints lookup failed:", type(exc).__name__, exc)

if not names:
    names = ["Alibaba", "Parasail", "Novita"]

MSGS = [
    {"role": "system", "content": _COMPILE_SYSTEM},
    {"role": "user", "content": "User intent:\nSummarize the coverage limits."},
]


def sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


def stream_once(provider):
    kwargs = dict(
        model=MODEL,
        messages=MSGS,
        max_tokens=700,
        temperature=0.0,
        stream=True,
        stream_options={"include_usage": True},
        **extra,
    )
    kwargs["extra_body"] = {"provider": {"order": [provider], "allow_fallbacks": False}}
    st = litellm.completion(**kwargs)
    txt = ""
    for chunk in st:
        ch = chunk.choices[0] if chunk.choices else None
        if ch is not None and getattr(getattr(ch, "delta", None), "content", None):
            txt += str(ch.delta.content)
    return sha(txt), len(txt)


print("\n== 3 streaming litellm calls per provider, temp=0.0, real compile prompt ==")
for p in names:
    hashes = []
    try:
        for _ in range(3):
            h, n = stream_once(p)
            hashes.append(h[:16])
        print("  %-14s distinct=%d/3  %s" % (p, len(set(hashes)), hashes))
    except Exception as exc:
        print("  %-14s ERROR %s: %s" % (p, type(exc).__name__, str(exc)[:160]))
