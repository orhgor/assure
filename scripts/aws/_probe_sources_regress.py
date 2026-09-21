"""0.3 — the sources-list regression.

(a) the list route's rows and the exact predicate the shell applies to them,
(b) search before and after a source delete,
(c) delete still works,
(d) the export names every source the compile used.
"""
import json, os, re, sqlite3, urllib.error, urllib.request, uuid

BASE = "http://127.0.0.1:8891"
FIXTURE = "docs/demo/insurance-boston-real-estate/assets/naic-underwriting-policy-redacted.md"
QUERIES = ["deductible", "wind", "liability"]
INTENT = ("Summarize the Massachusetts commercial real estate underwriting obligations in the "
          "source. Report the wind/hail deductible percentage, the maximum liability in USD, and "
          "the inspection interval in months. Cite each figure.")


def gate_key():
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r'SHELL_ACCESS_KEY\s*=\s*["\']?([^"\'\n]+)', raw)
    return m.group(1).strip() if m else ""


def call(method, path, body=None, ctype="application/json", timeout=300):
    data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"X-Shell-Key": gate_key(), "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def upload(pid, name, payload):
    b = "----assureprobe" + uuid.uuid4().hex
    form = b"".join([
        f"--{b}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode(),
        b"Content-Type: text/markdown\r\n\r\n", payload, b"\r\n", f"--{b}--\r\n".encode()])
    return call("POST", f"/api/projects/{pid}/substrate/upload", form,
                f"multipart/form-data; boundary={b}")


def shell_posted(rows):
    """shell.js:3009-3011 — rows.filter(f => f.included !== false).map(f => f.id)"""
    return [r["id"] for r in rows if r.get("included", None) is not False]


print("=" * 74)
print("(a) the list route on wave2-regress, and what the shell would post")
st, body = call("GET", "/api/projects/wave2-regress/substrate")
rows = (json.loads(body) or {}).get("files") or []
print("   http %s rows=%d" % (st, len(rows)))
for r in rows:
    print("     id=%-26s included=%-6s file=%r" % (r["id"], r["included"], r["filename"]))
print("   shell posts: %s" % shell_posted(rows))
print("   types: %s" % [(r["filename"], type(r["included"]).__name__) for r in rows])

print("\n" + "=" * 74)
print("(b)/(c) scratch project: two sources, one excluded, compile, export, delete")
st, body = call("POST", "/api/projects", {"title": "wave3-sources"})
pid = json.loads(body)["id"]
payload = open(FIXTURE, "rb").read()
for name in ("src-included.md", "src-excluded.md"):
    st, b2 = upload(pid, name, payload)
    print("   upload %-18s -> %s" % (name, st))
st, body = call("GET", f"/api/projects/{pid}/substrate")
rows = (json.loads(body) or {}).get("files") or []
excluded = [r for r in rows if "excluded" in r["filename"]]
included = [r for r in rows if "included" in r["filename"] and "excluded" not in r["filename"]]
st, b2 = call("PATCH", f"/api/projects/{pid}/substrate/{excluded[0]['id']}", {"included": False})
print("   PATCH excluded -> %s %s" % (st, b2.strip()))
st, body = call("GET", f"/api/projects/{pid}/substrate")
rows = (json.loads(body) or {}).get("files") or []
posted = shell_posted(rows)
print("   shell posts after the toggle: %s  (excluded id %s present? %s)"
      % (posted, excluded[0]["id"], excluded[0]["id"] in posted))

# search before the delete (the shell's dock search route)
print("\n   -- search before delete --")
before = {}
for q in QUERIES:
    st, b2 = call("POST", f"/api/projects/{pid}/jdf/search", {"query": q})
    j = json.loads(b2 or "{}")
    before[q] = j.get("count")
    print("     %-12r -> %s count=%s" % (q, st, j.get("count")))

# compile with only the included source, then export
req = urllib.request.Request(BASE + f"/api/projects/{pid}/draft/stream",
    data=json.dumps({"intent": INTENT, "compileType": "full",
                     "substrate_file_ids": [included[0]["id"]]}).encode(),
    headers={"Content-Type": "application/json", "X-Shell-Key": gate_key()}, method="POST")
frames = []
with urllib.request.urlopen(req, timeout=600) as resp:
    for line in resp:
        ln = line.decode("utf-8", "replace").rstrip("\n")
        if ln.startswith("data: "):
            try: frames.append(json.loads(ln[6:]))
            except Exception: pass
ver = next((f for f in frames if isinstance(f, dict) and f.get("type") == "verified"), {})
print("\n   compile with [%s] -> stats=%s" % (included[0]["id"], json.dumps(ver.get("provenance_stats") or {})))

st, body = call("GET", f"/api/projects/{pid}/export?format=json")
st_json = st
doc = json.loads(body or "{}")
names = set()
stack = [doc.get("document") or {}]
while stack:
    n = stack.pop()
    if isinstance(n, dict):
        for p in n.get("provenance") or []:
            if isinstance(p, dict) and p.get("filename"):
                names.add(p["filename"])
        stack.extend(n.get("children") or [])
print("   export json http %s — provenance filenames: %s" % (st_json, sorted(names)))
print("   export names the source the compile used? %s" % (included[0]["filename"] in names or "src-included.md" in names))
print("   export names the EXCLUDED source too? %s" % ("src-excluded.md" in names))

# delete the source the compile used, through the API
st, body = call("DELETE", f"/api/projects/{pid}/substrate/{included[0]['id']}")
print("\n   DELETE substrate %s -> %s %s" % (included[0]["id"], st, body.strip()[:80]))
st, body = call("GET", f"/api/projects/{pid}/substrate")
left = (json.loads(body) or {}).get("files") or []
print("   vault now: %s" % [(r["id"], r["filename"], r["included"]) for r in left])
print("   deleted source gone? %s" % (included[0]["id"] not in [r["id"] for r in left]))

print("\n   -- search after delete --")
for q in QUERIES:
    st, b2 = call("POST", f"/api/projects/{pid}/jdf/search", {"query": q})
    j = json.loads(b2 or "{}")
    print("     %-12r -> %s count=%s (before=%s, same=%s)" % (q, st, j.get("count"), before[q], j.get("count") == before[q]))

# export again after the delete
st, body = call("GET", f"/api/projects/{pid}/export?format=json")
print("\n   export after delete: http %s ok=%s" % (st, json.loads(body or "{}").get("ok")))

db = sqlite3.connect("prompt_matrix/history.sqlite")
db.execute("DELETE FROM projects WHERE id = ?", (pid,))
db.commit()
print("\n   scratch project %s removed" % pid)
