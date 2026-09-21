"""B2b probe: pin OpenRouter routing to one upstream provider and see if the
compile prompt becomes byte-stable at temperature=0.0.

A: raw OpenRouter, provider.order=["Alibaba"], allow_fallbacks=False, x3
B: raw OpenRouter, provider.order=["Parasail"], allow_fallbacks=False, x3
C: litellm with extra_body pinning, using the REAL compile system prompt, x3
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
extra = litellm_kwargs_for(provider_slug_for_litellm(MODEL))
OR_KEY = os.environ.get("OPENROUTER_API_KEY") or ""
OR_URL = (extra.get("api_base") or "https://openrouter.ai/api/v1").rstrip("/") + "/chat/completions"
OR_MODEL = MODEL.split("/", 1)[1]


def sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


PROMPT = (
    "Write four short paragraphs of prose about commercial property "
    "underwriting in Massachusetts. Use headings. Do not use any numbers."
)
MSGS = [{"role": "user", "content": PROMPT}]

# The real compile call: same system prompt the route uses.
REAL_MSGS = [
    {"role": "system", "content": _COMPILE_SYSTEM},
    {"role": "user", "content": "User intent:\nSummarize the coverage limits."},
]


def raw(label, provider, msgs, temperature=0.0):
    body = {
        "model": OR_MODEL,
        "messages": msgs,
        "max_tokens": 700,
        "temperature": temperature,
        "provider": {"order": [provider], "allow_fallbacks": False},
    }
    r = requests.post(
        OR_URL,
        headers={"Authorization": "Bearer " + OR_KEY, "Content-Type": "application/json"},
        json=body,
        timeout=180,
    )
    if r.status_code != 200:
        print("  %-22s HTTP=%d body=%s" % (label, r.status_code, r.text[:200]))
        return None
    j = r.json()
    txt = ((j.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    print("  %-22s provider=%-10r sha=%s len=%d" % (label, j.get("provider"), sha(txt)[:16], len(txt)))
    return sha(txt)


def lite(label, msgs, pin, temperature=0.0):
    body = {"provider": {"order": [pin], "allow_fallbacks": False}} if pin else {}
    r = litellm.completion(
        model=MODEL,
        messages=msgs,
        max_tokens=700,
        temperature=temperature,
        extra_body=body,
        **extra,
    )
    txt = r.choices[0].message.content or ""
    print("  %-22s pin=%-10s sha=%s len=%d" % (label, pin, sha(txt)[:16], len(txt)))
    return sha(txt)


print("== A. raw, pinned to Alibaba, temp=0.0, generic prompt (x3) ==")
a = [raw("alibaba-%d" % i, "Alibaba", MSGS) for i in (1, 2, 3)]
print("  distinct:", len(set(x for x in a if x)), "/3")

print("\n== B. raw, pinned to Parasail, temp=0.0, generic prompt (x3) ==")
b = [raw("parasail-%d" % i, "Parasail", MSGS) for i in (1, 2, 3)]
print("  distinct:", len(set(x for x in b if x)), "/3")

print("\n== C. litellm + extra_body pin, REAL compile system prompt, temp=0.0 (x3) ==")
c = [lite("real-alibaba-%d" % i, REAL_MSGS, "Alibaba") for i in (1, 2, 3)]
print("  distinct:", len(set(c)), "/3")

print("\n== D. control: litellm + NO pin, real compile prompt, temp=0.0 (x3) ==")
d = [lite("real-unpinned-%d" % i, REAL_MSGS, None) for i in (1, 2, 3)]
print("  distinct:", len(set(d)), "/3")
