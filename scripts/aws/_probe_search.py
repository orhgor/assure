"""Confirm the vault + memory layer return the seeded source.
usage: _probe_search.py <project_id> <query>
"""
import json, os, re, sys, urllib.parse, urllib.request

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")

def gate_key():
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""

def get(path):
    req = urllib.request.Request(BASE + path, headers={"X-Shell-Key": gate_key()})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

pid, query = sys.argv[1], sys.argv[2]
st, body = get(f"/api/projects/{pid}/substrate")
print("GET /api/projects/%s/substrate -> %s" % (pid, st))
try:
    rows = json.loads(body)
    items = rows if isinstance(rows, list) else (rows.get("files") or rows.get("substrate") or [])
    for it in items:
        print("  ", {k: it.get(k) for k in ("id", "filename", "size_bytes", "file_size_bytes", "included", "instruction_like", "page_count")})
except Exception as exc:
    print(body[:800])
st, body = get("/api/omp/recall?key=" + urllib.parse.quote(query))
print("GET /api/omp/recall?key=%s -> %s" % (query, st))
try:
    data = json.loads(body)
    res = data.get("results") or data.get("memories") or []
    print("  hits:", len(res))
    for r in res[:4]:
        text = json.dumps(r)[:400]
        print("  ", text)
except Exception as exc:
    print(body[:1200])
