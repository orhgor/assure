"""Does temperature=0 actually reach OpenRouter, and is the provider stable?

Three direct calls with identical messages and temperature=0.0, printing the
serving provider (litellm hidden params) and the output hash, then one call
WITHOUT temperature to show litellm's default is the provider's own.
"""
import hashlib, json, os
from dotenv import load_dotenv
for c in (".env.staging", ".env.production", ".env"):
    if os.path.exists(c):
        load_dotenv(c); break

import litellm
from prompt_matrix.keys import litellm_kwargs_for, provider_slug_for_litellm

MODEL = "openrouter/qwen/qwen3-next-80b-a3b-instruct"
MSGS = [{"role": "user", "content": "Reply with exactly this JSON and nothing else: {\"ok\": true, \"n\": 3}"}]
slug = provider_slug_for_litellm(MODEL)
extra = litellm_kwargs_for(slug)
print("slug=", slug, "extra keys=", sorted(extra))

def run(temperature, label):
    kwargs = {"model": MODEL, "messages": MSGS, "max_tokens": 64}
    if temperature is not None:
        kwargs["temperature"] = temperature
    r = litellm.completion(**kwargs, **extra)
    txt = r.choices[0].message.content or ""
    hp = getattr(r, "_hidden_params", {}) or {}
    prov = hp.get("custom_llm_provider") or ""
    print("  %-18s temp=%-5s len=%d sha=%s model=%r provider=%r" % (
        label, temperature, len(txt), hashlib.sha256(txt.encode()).hexdigest()[:12], r.model, prov))
    print("      text=%r" % txt[:120])
    return hashlib.sha256(txt.encode()).hexdigest()

print("\n-- temperature=0.0 x3 --")
h0 = [run(0.0, "run%d" % i) for i in (1, 2, 3)]
print("  distinct: %d/3" % len(set(h0)))
print("\n-- no temperature (provider default) x2 --")
hn = [run(None, "run%d" % i) for i in (1, 2)]
print("  distinct: %d/2" % len(set(hn)))
