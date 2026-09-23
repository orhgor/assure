"""0.3 (final) — provenance is source_name, and what the JDF index actually holds."""
import json, re, sqlite3, urllib.error, urllib.request, uuid

BASE = "http://127.0.0.1:8891"
MD = "docs/demo/insurance-boston-real-estate/assets/naic-underwriting-policy-redacted.md"
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


def provenance(tree):
    names, stack = set(), [tree]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            for p in n.get("provenance") or []:
                if isinstance(p, dict):
                    v = p.get("source_name") or p.get("filename")
                    if v:
                        names.add(str(v))
            for k in ("body", "children"):
                if isinstance(n.get(k), list):
                    stack.extend(n[k])
    return names


raw = open(MD, "rb").read()
st, body = call("POST", "/api/projects", {"title": "wave3-regress4"})
pid = json.loads(body)["id"]
print("scratch project %s" % pid)
st, b2 = upload(pid, "export-src.md", raw)
print("upload export-src.md -> %s" % st)
st, body = call("GET", f"/api/projects/{pid}/substrate")
sub = ((json.loads(body) or {}).get("files") or [{}])[0]["id"]
print("substrate id %s" % sub)

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
print("compile stats=%s" % json.dumps(ver.get("provenance_stats") or {}))
print("verified-frame source_names: %s" % sorted(provenance(ver.get("document") or {})))

st, body = call("GET", f"/api/projects/{pid}/export?format=json")
doc = json.loads(body or "{}")
enames = provenance(doc.get("document") or {})
print("export json http %s source_names: %s" % (st, sorted(enames)))
print("export names every source the compile used? %s" % ("export-src.md" in enames))
st, mdb = call("GET", f"/api/projects/{pid}/export?format=md")
print("export md http %s names the source? %s" % (st, "export-src.md" in mdb))

# what does the JDF index hold at all?
try:
    omp = sqlite3.connect("/home/ubuntu/.omp/omp.db")
    tables = [r[0] for r in omp.execute("select name from sqlite_master where type='table'")]
    print("\nOMP tables: %s" % tables)
    for t in tables:
        cols = [r[1] for r in omp.execute("PRAGMA table_info(%s)" % t)]
        if any(c in cols for c in ("namespace", "tenant_id", "tags")):
            key = next(c for c in ("namespace", "tenant_id", "tags") if c in cols)
            print("  %s by %s: %s" % (t, key, omp.execute(
                "select %s, count(*) from %s group by %s order by 2 desc limit 8" % (key, t, key)).fetchall()))
except Exception as exc:
    print("omp inspect failed:", exc)

db = sqlite3.connect("prompt_matrix/history.sqlite")
db.execute("DELETE FROM projects WHERE id = ?", (pid,))
db.commit()
print("\nscratch project removed")
