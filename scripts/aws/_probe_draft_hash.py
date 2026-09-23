"""Is the DRAFT varying, or only the anchoring? 3 compiles, cache cleared each run.

Prints, per run: draft sha256 + length, the pipeline's model id, and the
provenance vector. If the draft hashes are identical and the vector still moves,
the variance is downstream of the draft and the temperature fix is not the lever.
"""
import hashlib, json, os, re, sqlite3, sys, time, urllib.request

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")
PROJECT = "demo-3235f5"
SOURCE_ID = "sub-d3eab1f0fa9c486d"
INTENT_FILE = "/home/ubuntu/probes/demo_intent.txt"
OMP_DB = "/home/ubuntu/.omp/omp.db"
RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
FIELDS = ["eligible", "anchored", "supported", "partial", "unverified"]


def gate_key():
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""


def clear(pid):
    app = sqlite3.connect("prompt_matrix/history.sqlite")
    n = app.execute("delete from pipeline_cache where project_id=? and kind='ast'", (pid,)).rowcount
    app.commit(); app.close()
    m = 0
    try:
        omp = sqlite3.connect(OMP_DB)
        rows = omp.execute("select rowid from memories where tags like ?", (f'%"ast:{pid}:%',)).fetchall()
        for r in rows:
            omp.execute("delete from memories where rowid=?", (r[0],))
        omp.commit(); omp.close(); m = len(rows)
    except Exception as exc:
        print("  [omp warn]", exc)
    return n, m


def post(path, body, key, timeout=600.0):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Shell-Key": key}, method="POST")
    frames = []
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for line in resp:
            text = line.decode("utf-8", "replace").rstrip("\n")
            if text.startswith("data: "):
                try: frames.append(json.loads(text[6:]))
                except Exception: pass
    return frames


intent = open(INTENT_FILE).read().strip()
print("intent sha256:", hashlib.sha256(intent.encode()).hexdigest())
hashes = []
for i in range(1, RUNS + 1):
    n, m = clear(PROJECT)
    t0 = time.time()
    frames = post(f"/api/projects/{PROJECT}/draft/stream",
                  {"intent": intent, "compileType": "full", "substrate_file_ids": [SOURCE_ID]}, gate_key())
    draft = "".join(str(f.get("delta") or "") for f in frames if f.get("type") == "token")
    dh = hashlib.sha256(draft.encode()).hexdigest()
    hashes.append(dh)
    model = ""
    for f in frames:
        if f.get("type") == "status" and f.get("model"): model = f["model"]
        if f.get("type") == "complete" and f.get("model"): model = f["model"]
    ver = next((f for f in frames if isinstance(f, dict) and f.get("type") == "verified"), {})
    ps = ver.get("provenance_stats") or {}
    print("\n--- run %d (%.1fs) cache=%s ---" % (i, time.time() - t0, {"pc": n, "omp": m}))
    print("  draft sha256=%s len=%d" % (dh[:16], len(draft)))
    print("  model=%r" % model)
    print("  vector=%s" % json.dumps({k: ps.get(k) for k in FIELDS}))
    open("/tmp/draft_%d.txt" % i, "w").write(draft)
print("\nDISTINCT draft hashes: %d of %d -> %s" % (len(set(hashes)), RUNS, [h[:12] for h in hashes]))
