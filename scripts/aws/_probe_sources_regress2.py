"""0.3 (corrected) — ingest so search has an index, and walk the export tree's body."""
import json, os, re, sqlite3, urllib.error, urllib.request, uuid

BASE = "http://127.0.0.1:8891"
MD = "docs/demo/insurance-boston-real-estate/assets/naic-underwriting-policy-redacted.md"
PDF = "docs/demo/insurance-boston-real-estate/assets/naic-underwriting-policy-redacted.pdf"
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


def upload_md(pid, name, payload):
    b = "----assureprobe" + uuid.uuid4().hex
    form = b"".join([
        f"--{b}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode(),
        b"Content-Type: text/markdown\r\n\r\n", payload, b"\r\n", f"--{b}--\r\n".encode()])
    return call("POST", f"/api/projects/{pid}/substrate/upload", form,
                f"multipart/form-data; boundary={b}")


def ingest(pid, payload):
    b = "----assureprobe" + uuid.uuid4().hex
    form = b"".join([
        f"--{b}\r\n".encode(),
        b'Content-Disposition: form-data; name="file"; filename="search-fixture.pdf"\r\n',
        b"Content-Type: application/pdf\r\n\r\n", payload, b"\r\n", f"--{b}--\r\n".encode()])
    return call("POST", f"/api/projects/{pid}/jdf/ingest", form,
                f"multipart/form-data; boundary={b}")


def search(pid):
    out = {}
    for q in QUERIES:
        st, body = call("POST", f"/api/projects/{pid}/jdf/search", {"query": q})
        out[q] = json.loads(body or "{}").get("count")
    return out


def provenance_names(tree):
    names, prov_keys = set(), set()
    stack = [tree]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            for p in n.get("provenance") or []:
                if isinstance(p, dict):
                    prov_keys |= set(p.keys())
                    if p.get("filename"):
                        names.add(p["filename"])
            stack.extend(n.get("body") if isinstance(n.get("body"), list) else [])
            stack.extend(n.get("children") if isinstance(n.get("children"), list) else [])
    return names, prov_keys


st, body = call("POST", "/api/projects", {"title": "wave3-sources2"})
pid = json.loads(body)["id"]
print("scratch project %s" % pid)

st, b2 = ingest(pid, open(PDF, "rb").read())
print("POST /jdf/ingest -> %s %s" % (st, b2.strip()[:200]))

st, body = call("GET", f"/api/projects/{pid}/substrate")
rows = (json.loads(body) or {}).get("files") or []
print("vault after ingest: %s" % [(r["id"], r["filename"], r["included"]) for r in rows])
if not rows:
    st, b2 = upload_md(pid, "search-src.md", open(MD, "rb").read())
    print("   (no vault row from ingest) uploaded markdown -> %s" % st)
    st, body = call("GET", f"/api/projects/{pid}/substrate")
    rows = (json.loads(body) or {}).get("files") or []
    print("   vault: %s" % [(r["id"], r["filename"], r["included"]) for r in rows])

print("\n-- search BEFORE the source delete --")
before = search(pid)
print("   counts: %s" % before)

sub = rows[0]["id"]
st, b2 = call("DELETE", f"/api/projects/{pid}/substrate/{sub}")
print("\nDELETE substrate %s -> %s %s" % (sub, st, b2.strip()[:70]))
st, body = call("GET", f"/api/projects/{pid}/substrate")
print("vault now: %s" % [(r["id"], r["filename"]) for r in (json.loads(body) or {}).get("files") or []])

print("\n-- search AFTER the source delete --")
after = search(pid)
print("   counts: %s" % after)
print("   identical: %s" % (before == after))

db = sqlite3.connect("prompt_matrix/history.sqlite")
db.execute("DELETE FROM projects WHERE id = ?", (pid,))
db.commit()

# --- export names the sources the compile used ---
print("\n" + "=" * 74)
print("export provenance filenames")
st, body = call("POST", "/api/projects", {"title": "wave3-export"})
pid2 = json.loads(body)["id"]
st, b2 = upload_md(pid2, "export-src.md", open(MD, "rb").read())
print("   upload -> %s" % st)
raw = open(MD, "rb").read()
payload = json.dumps({"intent": INTENT, "compileType": "full"}).encode()
req = urllib.request.Request(BASE + f"/api/projects/{pid2}/draft/stream", data=payload,
    headers={"Content-Type": "application/json", "X-Shell-Key": gate_key()}, method="POST")
frames = []
with urllib.request.urlopen(req, timeout=600) as resp:
    for line in resp:
        ln = line.decode("utf-8", "replace").rstrip("\n")
        if ln.startswith("data: "):
            try: frames.append(json.loads(ln[6:]))
            except Exception: pass
ver = next((f for f in frames if isinstance(f, dict) and f.get("type") == "verified"), {})
print("   compile (no substrate ids) stats=%s" % json.dumps(ver.get("provenance_stats") or {}))

st, body = call("GET", f"/api/projects/{pid2}/export?format=json")
doc = json.loads(body or "{}")
names, keys = provenance_names(doc.get("document") or {})
print("   export json http %s" % st)
print("   provenance filenames: %s" % sorted(names))
print("   provenance row keys: %s" % sorted(keys))
st, body = call("GET", f"/api/projects/{pid2}/export?format=md")
print("   export md http %s contains 'export-src.md'? %s" % (st, "export-src.md" in body))

db = sqlite3.connect("prompt_matrix/history.sqlite")
db.execute("DELETE FROM projects WHERE id = ?", (pid2,))
db.commit()
print("\n   scratch projects removed")
