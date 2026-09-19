"""Delete a substrate row through the gate.
usage: _probe_delete_source.py <project_id> <file_id>
"""
import os, re, sys, urllib.request

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")

def gate_key():
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""

pid, fid = sys.argv[1], sys.argv[2]
req = urllib.request.Request(f"{BASE}/api/projects/{pid}/substrate/{fid}",
                             headers={"X-Shell-Key": gate_key()}, method="DELETE")
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        print("http", r.status, r.read().decode()[:400])
except urllib.error.HTTPError as e:
    print("http", e.code, e.read().decode()[:400])
req = urllib.request.Request(f"{BASE}/api/projects/{pid}/substrate",
                             headers={"X-Shell-Key": gate_key()})
with urllib.request.urlopen(req, timeout=60) as r:
    print("remaining:", r.read().decode()[:600])
