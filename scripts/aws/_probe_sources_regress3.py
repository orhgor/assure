"""0.3 (corrected again) — ingest the markdown so the index has chunks, and compile
WITH the substrate id before exporting."""
import json, re, sqlite3, urllib.error, urllib.request, uuid

BASE = "http://127.0.0.1:8891"
MD = "docs/demo/insurance-boston-real-estate/assets/naic-underwriting-policy-redacted.md"
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


def post_file(pid, path_suffix, name, payload, ctype):
    b = "----assureprobe" + uuid.uuid4().hex
    form = b"".join([
        f"--{b}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode(),
        f"Content-Type: {ctype}\r\n\r\n".encode(), payload, b"\r\n", f"--{b}--\r\n".encode()])
    return call("POST", f"/api/projects/{pid}{path_suffix}", form,
                f"multipart/form-data; boundary={b}")


def search(pid):
    return {q: json.loads(call("POST", f"/api/projects/{pid}/jdf/search", {"query": q})[1] or "{}").get("count")
            for q in QUERIES}


def provenance(tree):
    names, keys = set(), set()
    stack = [tree]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            for p in n.get("provenance") or []:
                if isinstance(p, dict):
                    keys |= set(p.keys())
                    if p.get("filename"):
                        names.add(p["filename"])
            for k in ("body", "children"):
                if isinstance(n.get(k), list):
                    stack.extend(n[k])
    return names, keys


raw = open(MD, "rb").read()
st, body = call("POST", "/api/projects", {"title": "wave3-regress3"})
pid = json.loads(body)["id"]
print("scratch project %s" % pid)

st, b2 = post_file(pid, "/jdf/ingest", "naic.md", raw, "text/markdown")
j = json.loads(b2 or "{}")
print("ingest md -> %s chunks_total=%s stored=%s failed=%s" % (st, j.get("chunks_total"), j.get("chunks_stored"), j.get("chunks_failed")))
st, b2 = post_file(pid, "/substrate/upload", "regress-src.md", raw, "text/markdown")
print("upload md -> %s" % st)
st, body = call("GET", f"/api/projects/{pid}/substrate")
rows = (json.loads(body) or {}).get("files") or []
print("vault: %s" % [(r["id"], r["filename"], r["included"]) for r in rows])
sub = rows[0]["id"]

print("\n-- search BEFORE delete --")
before = search(pid); print("   %s" % before)

# compile WITH the substrate id, then export
req = urllib.request.Request(BASE + f"/api/projects/{pid}/draft/stream",
    data=json.dumps({"intent": INTENT, "compileType": "full", "substrate_file_ids": [sub]}).encode(),
    headers={"Content-Type": "application/json", "X-Shell-Key": gate_key()}, method="POST")
frames = []
with urllib.request.urlopen(req, timeout=600) as resp:
    for line in resp:
        ln = line.decode("utf-8", "replace").rstrip("\n")
        if ln.startswith("data: "):
            try: frames.append(json.loads(ln[6:]))
            except Exception: pass
ver = next((f for f in frames if isinstance(f, dict) and f.get("type") == "verified"), {})
print("\ncompile with [%s] stats=%s" % (sub, json.dumps(ver.get("provenance_stats") or {})))
vnames, vkeys = provenance(ver.get("document") or {})
print("   verified-frame provenance filenames: %s" % sorted(vnames))
print("   provenance row keys: %s" % sorted(vkeys))

st, body = call("GET", f"/api/projects/{pid}/export?format=json")
doc = json.loads(body or "{}")
enames, ekeys = provenance(doc.get("document") or {})
print("   export json http %s provenance filenames: %s" % (st, sorted(enames)))
print("   export names every source the compile used? %s" % ("regress-src.md" in enames))
st, mdb = call("GET", f"/api/projects/{pid}/export?format=md")
print("   export md http %s names the source? %s" % (st, "regress-src.md" in mdb))

st, b2 = call("DELETE", f"/api/projects/{pid}/substrate/{sub}")
print("\nDELETE substrate %s -> %s %s" % (sub, st, b2.strip()[:60]))
st, body = call("GET", f"/api/projects/{pid}/substrate")
print("   vault now: %s" % [(r["id"], r["filename"]) for r in (json.loads(body) or {}).get("files") or []])

print("\n-- search AFTER delete --")
after = search(pid); print("   %s" % after)
print("   identical: %s" % (before == after))

db = sqlite3.connect("prompt_matrix/history.sqlite")
db.execute("DELETE FROM projects WHERE id = ?", (pid,))
db.commit()
print("\n   scratch project removed")
