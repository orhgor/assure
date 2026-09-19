"""B2 probe: is the compile non-determinism provider-side, and does a seed fix it?

Prints, in order:
  1. litellm's get_optional_params(...)['temperature'] for temperature=0.0
  2. three consecutive litellm calls at temperature=0.0 -> sha256 of the text
  3. the same three calls with seed=<int> added -> sha256
  4. the upstream provider OpenRouter actually served each call (raw JSON `provider`)
"""
import hashlib, json, os
from dotenv import load_dotenv
for c in (".env.staging", ".env.production", ".env"):
    if os.path.exists(c):
        load_dotenv(c)
        break

import litellm
import importlib.metadata as _md
print("litellm", _md.version("litellm"))
from prompt_matrix.keys import litellm_kwargs_for, provider_slug_for_litellm

MODEL = "openrouter/qwen/qwen3-next-80b-a3b-instruct"
print("MODEL =", MODEL)
slug = provider_slug_for_litellm(MODEL)
print("provider_slug =", slug)
extra = litellm_kwargs_for(slug)
print("extra keys =", sorted(extra.keys()))
print("api_base =", extra.get("api_base"))

# ---- 1. what litellm turns our kwargs into -------------------------------
op = dict(
    litellm.utils.get_optional_params(
        model=MODEL,
        custom_llm_provider="openrouter",
        temperature=0.0,
        max_tokens=256,
        stream=False,
    )
)
print("optional_params['temperature'] =", repr(op.get("temperature")))
print("optional_params keys =", sorted(op.keys()))

PROMPT = (
    "Write four short paragraphs of prose about commercial property "
    "underwriting in Massachusetts. Use headings. Do not use any numbers."
)
MSGS = [{"role": "user", "content": PROMPT}]


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def litellm_call(label, temperature, seed=None):
    kwargs = {"model": MODEL, "messages": MSGS, "max_tokens": 400}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if seed is not None:
        kwargs["seed"] = seed
    r = litellm.completion(**kwargs, **extra)
    txt = r.choices[0].message.content or ""
    hp = getattr(r, "_hidden_params", {}) or {}
    print(
        "  %-18s temp=%-4s seed=%-6s sha=%s len=%d model=%r"
        % (label, temperature, seed, sha(txt)[:16], len(txt), getattr(r, "model", None))
    )
    # litellm keeps the provider's own response bits under hidden params; print
    # the lot, untruncated, so the serving provider is on the record.
    print("      hidden_keys=%s" % sorted(hp.keys()))
    for k in ("custom_llm_provider", "provider", "model_id", "api_base"):
        if k in hp:
            print("      %s=%r" % (k, hp[k]))
    return sha(txt)


import requests

OR_KEY = os.environ.get("OPENROUTER_API_KEY") or ""
OR_URL = (extra.get("api_base") or "https://openrouter.ai/api/v1").rstrip("/") + "/chat/completions"
OR_MODEL = MODEL.split("/", 1)[1] if MODEL.startswith("openrouter/") else MODEL


def raw_call(label, temperature, seed=None):
    body = {"model": OR_MODEL, "messages": MSGS, "max_tokens": 400, "temperature": temperature}
    if seed is not None:
        body["seed"] = seed
    resp = requests.post(
        OR_URL,
        headers={"Authorization": "Bearer " + OR_KEY, "Content-Type": "application/json"},
        json=body,
        timeout=120,
    )
    print("  %-18s HTTP=%d" % (label, resp.status_code))
    if resp.status_code != 200:
        print("      body=%s" % resp.text[:300])
        return None
    j = resp.json()
    txt = ((j.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    print(
        "      provider=%r id=%r sha=%s len=%d"
        % (j.get("provider"), j.get("id"), sha(txt)[:16], len(txt))
    )
    return sha(txt)


print("\n== A. litellm, temperature=0.0, no seed (x3) ==")
base = [litellm_call("t0-run%d" % i, 0.0) for i in (1, 2, 3)]
print("  distinct:", len(set(base)), "/3")

print("\n== B. litellm, temperature=0.0, seed=42 (x3) ==")
seeded = [litellm_call("t0s-run%d" % i, 0.0, seed=42) for i in (1, 2, 3)]
print("  distinct:", len(set(seeded)), "/3")

print("\n== C. raw OpenRouter, temperature=0.0, no seed (x3) — shows the upstream provider ==")
raw = [raw_call("raw-run%d" % i, 0.0) for i in (1, 2, 3)]
print("  distinct:", len(set(x for x in raw if x)), "/3")

print("\n== D. raw OpenRouter, temperature=0.0, seed=42 (x3) ==")
raws = [raw_call("raws-run%d" % i, 0.0, seed=42) for i in (1, 2, 3)]
print("  distinct:", len(set(x for x in raws if x)), "/3")
