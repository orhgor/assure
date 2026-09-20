"""Does temperature=0.0 survive into the OpenRouter request body, and does greedy
decoding actually repeat on a substantive prompt?"""
import hashlib, json, os
from dotenv import load_dotenv
for c in (".env.staging", ".env.production", ".env"):
    if os.path.exists(c):
        load_dotenv(c); break

import litellm
import importlib.metadata as _md
print("litellm", _md.version("litellm"))
from prompt_matrix.keys import litellm_kwargs_for, provider_slug_for_litellm

MODEL = "openrouter/qwen/qwen3-next-80b-a3b-instruct"
slug = provider_slug_for_litellm(MODEL)
extra = litellm_kwargs_for(slug)

# 1. what litellm turns our kwargs into for this provider
try:
    op = litellm.utils.get_optional_params(
        model=MODEL, custom_llm_provider="openrouter",
        temperature=0.0, max_tokens=256, stream=False)
    op = dict(op)
    print("optional_params temperature:", repr(op.get("temperature")))
    print("optional_params keys:", sorted(op.keys()))
except Exception as exc:
    print("get_optional_params failed:", type(exc).__name__, exc)

PROMPT = ("Write four short paragraphs of prose about commercial property "
          "underwriting in Massachusetts. Use headings. Do not use any numbers.")
MSGS = [{"role": "user", "content": PROMPT}]

def run(temperature, label):
    kwargs = {"model": MODEL, "messages": MSGS, "max_tokens": 400}
    if temperature is not None:
        kwargs["temperature"] = temperature
    r = litellm.completion(**kwargs, **extra)
    txt = r.choices[0].message.content or ""
    hp = getattr(r, "_hidden_params", {}) or {}
    print("  %-8s temp=%-5s sha=%s len=%d" % (label, temperature,
          hashlib.sha256(txt.encode()).hexdigest()[:14], len(txt)))
    print("      hidden_params=%s" % json.dumps({k: str(v)[:120] for k, v in hp.items()})[:400])
    return hashlib.sha256(txt.encode()).hexdigest()

print("\n-- temperature=0.0, substantive prompt, x3 --")
h = [run(0.0, "t0-run%d" % i) for i in (1, 2, 3)]
print("  distinct: %d/3" % len(set(h)))
print("\n-- temperature=1.0 x2 (control: must differ) --")
h1 = [run(1.0, "t1-run%d" % i) for i in (1, 2)]
print("  distinct: %d/2" % len(set(h1)))
