"""0.3 search leg — the dock's search before and after a source delete.

The JDF index is tenant-scoped to the project id and only `default` has chunks
(the demo PDF has no text layer, and /jdf/ingest accepts PDFs only), so the
search runs on `default` while the delete happens on a scratch project: the
question is whether deleting a source disturbs the index at all.
"""
import json, re, sqlite3, urllib.error, urllib.request, uuid

BASE = "http://127.0.0.1:8891"
MD = "docs/demo/insurance-boston-real-estate/assets/naic-underwriting-policy-redacted.md"
QUERIES = ["policy", "premium", "exclusion", "deductible"]


def gate_key():
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r'SHELL_ACCESS_KEY\s*=\s*["\']?([^"\'\n]+)', raw)
    return m.group(1).strip() if m else ""


def call(method, path, body=None, ctype="application/json", timeout=120):
    data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"X-Shell-Key": gate_key(), "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def search(tenant):
    out = {}
    for q in QUERIES:
        st, body = call("POST", f"/api/projects/{tenant}/jdf/search", {"query": q})
        j = json.loads(body or "{}")
        out[q] = j.get("count")
    return out


print("-- search on tenant 'default' BEFORE any delete --")
before = search("default")
print("   %s" % before)

# a scratch project with two sources, one excluded, then delete one
raw = open(MD, "rb").read()
st, body = call("POST", "/api/projects", {"title": "wave3-search"})
pid = json.loads(body)["id"]
for name in ("keep.md", "drop.md"):
    b = "----assureprobe" + uuid.uuid4().hex
    form = b"".join([
        f"--{b}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode(),
        b"Content-Type: text/markdown\r\n\r\n", raw, b"\r\n", f"--{b}--\r\n".encode()])
    call("POST", f"/api/projects/{pid}/substrate/upload", form, f"multipart/form-data; boundary={b}")
st, body = call("GET", f"/api/projects/{pid}/substrate")
rows = (json.loads(body) or {}).get("files") or []
print("\nscratch %s vault: %s" % (pid, [(r["id"], r["filename"], r["included"]) for r in rows]))
drop = [r for r in rows if r["filename"] == "drop.md"][0]
st, b2 = call("DELETE", f"/api/projects/{pid}/substrate/{drop['id']}")
print("DELETE substrate %s (drop.md) -> %s %s" % (drop["id"], st, b2.strip()[:60]))
st, body = call("GET", f"/api/projects/{pid}/substrate")
print("vault now: %s" % [(r["id"], r["filename"]) for r in (json.loads(body) or {}).get("files") or []])

print("\n-- search on tenant 'default' AFTER the delete --")
after = search("default")
print("   %s" % after)
print("   identical to before: %s" % (before == after))
for q in QUERIES:
    print("     %-12r before=%-4s after=%-4s same=%s" % (q, before[q], after[q], before[q] == after[q]))

db = sqlite3.connect("prompt_matrix/history.sqlite")
db.execute("DELETE FROM projects WHERE id = ?", (pid,))
db.commit()
print("\nscratch project removed")
