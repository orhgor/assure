"""A4.5 search baseline — ingest the demo PDF into its own project, then search.

The dock's search is a JDF index search, and the ingest is PDF-only in the MVP.
Runs both calls through the gate and prints the raw search bodies.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")
PDF = "docs/demo/insurance-boston-real-estate/assets/naic-underwriting-policy-redacted.pdf"
PID = "wave2-search"
QUERIES = ["deductible", "wind", "Boston", "liability"]


def gate_key():
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""


def call(method, path, body=None, ctype="application/json"):
    data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                headers={"X-Shell-Key": gate_key(), "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


if sys.argv[1:] and sys.argv[1] == "--teardown":
    print("DELETE:", call("DELETE", "/api/projects/" + PID))
    sys.exit(0)

if sys.argv[1:] and sys.argv[1] == "--search-only":
    for q in QUERIES:
        st, body = call("POST", f"/api/projects/{PID}/jdf/search", {"query": q})
        print("search %-12r -> %s %s" % (q, st, body.strip()[:240]))
    sys.exit(0)

st, body = call("POST", "/api/projects", {"title": PID})
print("POST /api/projects -> %s %s" % (st, body[:160].strip()))
pid = json.loads(body)["id"]

payload = open(PDF, "rb").read()
boundary = "----assureprobe" + uuid.uuid4().hex
form = b"".join([
    f"--{boundary}\r\n".encode(),
    b'Content-Disposition: form-data; name="file"; filename="naic-underwriting-policy-redacted.pdf"\r\n',
    b"Content-Type: application/pdf\r\n\r\n", payload, b"\r\n",
    f"--{boundary}--\r\n".encode(),
])
st, body = call("POST", f"/api/projects/{pid}/jdf/ingest", form, f"multipart/form-data; boundary={boundary}")
print("POST /jdf/ingest -> %s %s" % (st, body.strip()[:400]))

for q in QUERIES:
    st, body = call("POST", f"/api/projects/{pid}/jdf/search", {"query": q})
    print("search %-12r -> %s %s" % (q, st, body.strip()[:240]))

st, body = call("GET", f"/api/projects/{pid}/substrate")
print("\nsubstrate after ingest:", [(r["id"], r["filename"], r["included"]) for r in (json.loads(body) or {}).get("files") or []])
