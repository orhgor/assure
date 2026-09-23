"""Raw OpenRouter calls at temperature=0: does greedy decoding repeat, and which
upstream provider served each call? Prints the endpoint list OpenRouter offers
for this model, then 3 identical calls with the response's `provider` field.
"""
import hashlib, json, os, urllib.request
from dotenv import load_dotenv
for c in (".env.staging", ".env.production", ".env"):
    if os.path.exists(c):
        load_dotenv(c); break

KEY = os.environ.get("OPENROUTER_API_KEY", "")
BASE = os.environ.get("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1")
MODEL = "qwen/qwen3-next-80b-a3b-instruct"
print("key present:", bool(KEY))


def call(path, body=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        method="POST" if body else "GET")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


try:
    eps = call(f"/models/{MODEL}/endpoints")
    names = [e.get("provider_name") for e in (eps.get("data") or {}).get("endpoints") or []]
    print("upstream endpoints OpenRouter serves for this model: %d" % len(names))
    print("  ", names)
    for e in (eps.get("data") or {}).get("endpoints") or []:
        print("   - %-24s quant=%-16s ctx=%s" % (e.get("provider_name"), e.get("quantization"), e.get("context_length")))
except Exception as exc:
    print("endpoints lookup failed:", type(exc).__name__, exc)

MSGS = [{"role": "user", "content": "Write four short paragraphs of prose about commercial property underwriting in Massachusetts. Use headings. Do not use any numbers."}]
print("\n-- temperature=0.0, identical request x3 --")
hashes, provs = [], []
for i in range(1, 4):
    r = call("/chat/completions", {"model": MODEL, "messages": MSGS, "temperature": 0.0, "max_tokens": 400})
    txt = (r["choices"][0]["message"] or {}).get("content") or ""
    prov = r.get("provider") or (r.get("openrouter_metadata") or {}).get("provider") or "?"
    h = hashlib.sha256(txt.encode()).hexdigest()
    hashes.append(h); provs.append(prov)
    print("  run%d provider=%-24s sha=%s len=%d" % (i, prov, h[:14], len(txt)))
print("  distinct outputs: %d/3" % len(set(hashes)))
print("  distinct providers: %d/3 -> %s" % (len(set(provs)), provs))
