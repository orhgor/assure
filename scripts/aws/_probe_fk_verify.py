"""C3 — create a scratch project, upload a source, compile, delete, and count the
rows the delete leaves behind across the nine tables Wave 2 found orphaned.

usage: _probe_fk_verify.py <label>
Cleans its own orphans afterwards so the live database is not left dirty.
"""
import json, os, re, sqlite3, sys, time, urllib.error, urllib.request, uuid

BASE = "http://127.0.0.1:8891"
FIXTURE = "docs/demo/insurance-boston-real-estate/assets/naic-underwriting-policy-redacted.md"
ORPHANS = ["jdf_revisions", "substrate_vault", "jdf_documents", "node_revisions", "audit_log",
           "project_budgets", "token_ledger_entries", "daily_compile_limits", "pipeline_cache"]
EXTRA = ["substrates", "project_comments", "workspace_settings", "drafts", "runs"]
LABEL = sys.argv[1] if len(sys.argv) > 1 else "wave3"
INTENT = ("Summarize the Massachusetts commercial real estate underwriting obligations in the "
          "source. Report the wind/hail deductible percentage, the maximum liability in USD, and "
          "the inspection interval in months. Cite each figure.")


def gate_key():
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r'SHELL_ACCESS_KEY\s*=\s*["\']?([^"\'\n]+)', raw)
    return m.group(1).strip() if m else ""


def call(method, path, body=None, ctype="application/json", timeout=180):
    data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"X-Shell-Key": gate_key(), "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def counts(db, pid, tables):
    out = {}
    for t in tables:
        cols = {r[1] for r in db.execute("PRAGMA table_info(%s)" % t)}
        key = "project_id" if "project_id" in cols else ("workspace_id" if "workspace_id" in cols else None)
        if not key:
            continue
        out[t] = db.execute("SELECT count(*) FROM %s WHERE %s = ?" % (t, key), (pid,)).fetchone()[0]
    return out


# 1. scratch project
st, body = call("POST", "/api/projects", {"title": "wave3-fk-%s" % LABEL})
pid = json.loads(body)["id"]
print("created project %s (http %s)" % (pid, st))

# 2. upload the fixture
payload = open(FIXTURE, "rb").read()
boundary = "----assureprobe" + uuid.uuid4().hex
form = b"".join([
    f"--{boundary}\r\n".encode(),
    f'Content-Disposition: form-data; name="file"; filename="fk-fixture.md"\r\n'.encode(),
    b"Content-Type: text/markdown\r\n\r\n", payload, b"\r\n", f"--{boundary}--\r\n".encode(),
])
st, body = call("POST", "/api/projects/%s/substrate/upload" % pid, form,
                "multipart/form-data; boundary=%s" % boundary)
print("upload -> %s %s" % (st, body[:140].strip()))
st, body = call("GET", "/api/projects/%s/substrate" % pid)
files = (json.loads(body) or {}).get("files") or []
subs = [f["id"] for f in files]
print("vault rows: %s" % [(f["id"], f["filename"], f["included"]) for f in files])

# 3. compile, so the downstream tables fill
req = urllib.request.Request(BASE + "/api/projects/%s/draft/stream" % pid,
    data=json.dumps({"intent": INTENT, "compileType": "full", "substrate_file_ids": subs}).encode(),
    headers={"Content-Type": "application/json", "X-Shell-Key": gate_key()}, method="POST")
t0 = time.time()
frames = []
with urllib.request.urlopen(req, timeout=600) as resp:
    for line in resp:
        ln = line.decode("utf-8", "replace").rstrip("\n")
        if ln.startswith("data: "):
            try: frames.append(json.loads(ln[6:]))
            except Exception: pass
ver = next((f for f in frames if isinstance(f, dict) and f.get("type") == "verified"), {})
print("compile: %.1fs stats=%s" % (time.time() - t0, json.dumps(ver.get("provenance_stats") or {})))

db = sqlite3.connect("prompt_matrix/history.sqlite")
before = counts(db, pid, ORPHANS + EXTRA)
print("\n-- rows before DELETE --")
for t in ORPHANS + EXTRA:
    if t in before:
        print("   %-22s %d" % (t, before[t]))

# 4. delete through the API (the route the shell uses)
st, body = call("DELETE", "/api/projects/%s" % pid)
print("\nDELETE /api/projects/%s -> %s %s" % (pid, st, body.strip()[:80]))
after = counts(db, pid, ORPHANS + EXTRA)

print("\n-- THE NINE TABLES: orphans left by the delete --")
print("   %-22s %8s %8s" % ("table", "before", "orphans"))
total = 0
for t in ORPHANS:
    if t in after:
        total += after[t]
        print("   %-22s %8d %8d" % (t, before.get(t, 0), after[t]))
print("   %-22s %8s %8d" % ("TOTAL", "", total))
print("\n-- the other project-scoped tables --")
for t in EXTRA:
    if t in after:
        print("   %-22s %8d %8d" % (t, before.get(t, 0), after[t]))

# 5. clean up so the live db is not left dirty
removed = []
for t in ORPHANS + EXTRA:
    cols = {r[1] for r in db.execute("PRAGMA table_info(%s)" % t)}
    key = "project_id" if "project_id" in cols else ("workspace_id" if "workspace_id" in cols else None)
    if not key:
        continue
    cur = db.execute("DELETE FROM %s WHERE %s = ?" % (t, key), (pid,))
    if cur.rowcount:
        removed.append("%s=%d" % (t, cur.rowcount))
db.execute("DELETE FROM projects WHERE id = ?", (pid,))
db.commit()
print("\ncleanup: %s" % (", ".join(removed) or "nothing left"))
print("ORPHAN_TOTAL=%d" % total)
