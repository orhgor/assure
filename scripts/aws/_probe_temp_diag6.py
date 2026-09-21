"""Why does the Alibaba pin hold non-streaming but not streaming?

1. raw OpenRouter with stream=True + provider pin, x3  -> does OpenRouter honour
   the pin while streaming? (If yes, litellm's streaming path is dropping it.)
2. litellm streaming with the pin, LITELLM_LOG=DEBUG -> show the request body
   litellm actually sends, so we can see whether `provider` is in it.
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
PIN = {"order": ["Alibaba"], "allow_fallbacks": False}
MSGS = [
    {"role": "system", "content": _COMPILE_SYSTEM},
    {"role": "user", "content": "User intent:\nSummarize the coverage limits."},
]


def sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


def raw_stream(label, pin):
    body = {"model": OR_MODEL, "messages": MSGS, "max_tokens": 700,
            "temperature": 0.0, "stream": True}
    if pin:
        body["provider"] = pin
    r = requests.post(
        OR_URL,
        headers={"Authorization": "Bearer " + OR_KEY, "Content-Type": "application/json"},
        json=body, stream=True, timeout=180,
    )
    if r.status_code != 200:
        print("  %-16s HTTP=%d %s" % (label, r.status_code, r.text[:200]))
        return None
    text, provider = "", None
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            break
        try:
            j = json.loads(payload)
        except Exception:
            continue
        provider = j.get("provider") or provider
        for ch in j.get("choices") or []:
            c = (ch.get("delta") or {}).get("content")
            if c:
                text += c
    print("  %-16s provider=%r sha=%s len=%d" % (label, provider, sha(text)[:16], len(text)))
    return sha(text)


print("== 1. RAW streaming, pinned to Alibaba, temp=0.0 (x3) ==")
a = [raw_stream("rawpin-%d" % i, PIN) for i in (1, 2, 3)]
print("  distinct:", len(set(x for x in a if x)), "/3")

print("\n== 2. litellm streaming with pin, DEBUG — what body is sent? ==")
os.environ["LITELLM_LOG"] = "DEBUG"
kwargs = dict(model=MODEL, messages=MSGS, max_tokens=700, temperature=0.0,
              stream=True, stream_options={"include_usage": True}, **extra)
kwargs["extra_body"] = {"provider": PIN}
r = litellm.completion(**kwargs)
txt = ""
for chunk in r:
    ch = chunk.choices[0] if chunk.choices else None
    if ch is not None and getattr(getattr(ch, "delta", None), "content", None):
        txt += str(ch.delta.content)
print("  litellm streaming pinned sha=%s len=%d" % (sha(txt)[:16], len(txt)))
