"""A4/A4.5 fixture — a scratch project with two sources, one excluded.

Creates `wave2-regress`, uploads the demo fixture twice under distinct names,
then marks the second one `included = false`, so the shell's grounding list has
one row that must be posted and one that must not. Prints the vault state.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")
FIXTURE = "docs/demo/insurance-boston-real-estate/assets/naic-underwriting-policy-redacted.md"
PID = "wave2-regress"


def gate_key():
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""


def call(method, path, body=None, ctype="application/json"):
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"X-Shell-Key": gate_key(), "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def upload(pid, remote_name, payload):
    boundary = "----assureprobe" + uuid.uuid4().hex
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{remote_name}"\r\n'.encode(),
        b"Content-Type: text/markdown\r\n\r\n", payload, b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return call("POST", f"/api/projects/{pid}/substrate/upload", body,
                f"multipart/form-data; boundary={boundary}")


if sys.argv[1:] and sys.argv[1] == "--teardown":
    for stray in ("wave2-regress", "wave2-regress-0a078e"):
        print("DELETE %s -> %s" % (stray, call("DELETE", "/api/projects/" + stray)))
    sys.exit(0)

st, body = call("POST", "/api/projects", {"title": "wave2-regress"})
print("POST /api/projects -> %s %s" % (st, body[:200].strip()))
PID = json.loads(body)["id"]
print("slugified project id:", PID)

payload = open(FIXTURE, "rb").read()
for name in ("naic-included.md", "naic-excluded.md"):
    st, body = upload(PID, name, payload)
    print("upload %-20s -> %s %s" % (name, st, body[:220].strip().replace("\n", " ")))

st, body = call("GET", f"/api/projects/{PID}/substrate")
rows = (json.loads(body) or {}).get("files") or []
print("\nvault rows for %s:" % PID)
for r in rows:
    print("  id=%s included=%s file=%r" % (r["id"], r["included"], r["filename"]))

excluded = [r for r in rows if "excluded" in (r["filename"] or "")]
if excluded:
    target = excluded[0]["id"]
    st, body = call("PATCH", f"/api/projects/{PID}/substrate/{target}", {"included": False})
    print("PATCH %s included=False -> %s %s" % (target, st, body.strip()))

st, body = call("GET", f"/api/projects/{PID}/substrate")
print("\nvault after toggle:")
for r in (json.loads(body) or {}).get("files") or []:
    print("  id=%s included=%s file=%r" % (r["id"], r["included"], r["filename"]))
