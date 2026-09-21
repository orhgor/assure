"""Create an empty project (no substrate) and print its id. usage: _probe_make_project.py <title>
"""
import json
import re
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8891"


def gate_key():
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""


title = sys.argv[1]
req = urllib.request.Request(
    BASE + "/api/projects", data=json.dumps({"title": title}).encode(), method="POST",
    headers={"X-Shell-Key": gate_key(), "Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        print(r.status, r.read().decode().strip())
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode()[:300])
