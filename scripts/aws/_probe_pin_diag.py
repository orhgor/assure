"""Would pinning one upstream provider make greedy decoding reproducible?
Evidence for the report only - no product code is changed here."""
import hashlib, json, os, urllib.request
from dotenv import load_dotenv
for c in (".env.staging", ".env.production", ".env"):
    if os.path.exists(c):
        load_dotenv(c); break
KEY = os.environ["OPENROUTER_API_KEY"]
BASE = os.environ.get("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1")
MODEL = "qwen/qwen3-next-80b-a3b-instruct"
MSGS = [{"role": "user", "content": "Write four short paragraphs of prose about commercial property underwriting in Massachusetts. Use headings. Do not use any numbers."}]

def call(body):
    req = urllib.request.Request(BASE + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())

def trial(label, order, fallbacks):
    hs, ps = [], []
    for i in (1, 2, 3):
        body = {"model": MODEL, "messages": MSGS, "temperature": 0.0, "max_tokens": 400,
                "provider": {"order": order, "allow_fallbacks": fallbacks}}
        r = call(body)
        txt = (r["choices"][0]["message"] or {}).get("content") or ""
        hs.append(hashlib.sha256(txt.encode()).hexdigest()); ps.append(r.get("provider"))
    print("  %-28s providers=%s distinct=%d/3" % (label, ps, len(set(hs))))
    return hs

print("-- pinned to one provider, allow_fallbacks=False --")
trial("order=[DeepInfra]", ["DeepInfra"], False)
trial("order=[Google]", ["Google"], False)
print("-- no pin (control) --")
trial("routing default", None, True)
