"""Phase B — measure the proportional gate: five genuine compiles of the demo intent.

The compile route caches the compiled tree per (project, intent+context, model,
PIPELINE_VERSION) in `pipeline_cache` (kind `ast`) plus an OMP memory, and a hit
replays the stored document and its `verified` payload without a model call. Five
runs of one intent therefore come back identical unless the cache is cleared, so
the cache is cleared before every run and each run is a real pipeline run.

Prints, per run: eligible / anchored / supported / partial / unanchored /
unverified, plus run 1's full provenance_stats and the gate fields.
"""
import json
import os
import re
import sqlite3
import statistics
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")
PROJECT = "demo-3235f5"
SOURCE_ID = "sub-d3eab1f0fa9c486d"
INTENT_FILE = "/home/ubuntu/probes/demo_intent.txt"
OMP_DB = "/home/ubuntu/.omp/omp.db"
RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 5
FIELDS = ["eligible", "anchored", "supported", "partial", "unanchored", "unverified"]


def gate_key():
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""


def clear_ast_cache(pid):
    app = sqlite3.connect("prompt_matrix/history.sqlite")
    n = app.execute("delete from pipeline_cache where project_id=? and kind='ast'", (pid,)).rowcount
    app.commit()
    app.close()
    m = 0
    try:
        omp = sqlite3.connect(OMP_DB)
        rows = omp.execute("select rowid from memories where tags like ?", (f'%"ast:{pid}:%',)).fetchall()
        for r in rows:
            omp.execute("delete from memories where rowid=?", (r[0],))
        omp.commit()
        omp.close()
        m = len(rows)
    except Exception as exc:
        print("  [omp clear warn] %s" % exc)
    return n, m


def post_stream(path, body, key, timeout=600.0):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Shell-Key": key}, method="POST")
    t0 = time.time()
    frames, status = [], None
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = resp.status
        for line in resp:
            text = line.decode("utf-8", "replace").rstrip("\n")
            if text.startswith("data: "):
                try:
                    frames.append(json.loads(text[6:]))
                except Exception:
                    pass
    return {"status": status, "elapsed": round(time.time() - t0, 2), "frames": frames}


def stats_of(frames):
    for f in frames:
        if isinstance(f, dict) and (f.get("type") == "verified" or "provenance_stats" in f):
            return f
    return {}


intent = open(INTENT_FILE).read().strip()
print("intent sha256:", __import__("hashlib").sha256(intent.encode()).hexdigest())
print("project=%s source=%s runs=%d" % (PROJECT, SOURCE_ID, RUNS))

rows = []
for i in range(1, RUNS + 1):
    n, m = clear_ast_cache(PROJECT)
    res = post_stream(f"/api/projects/{PROJECT}/draft/stream",
                      {"intent": intent, "compileType": "full", "substrate_file_ids": [SOURCE_ID]},
                      gate_key())
    ver = stats_of(res["frames"])
    ps = ver.get("provenance_stats") or {}
    err = next((f for f in res["frames"] if isinstance(f, dict) and f.get("type") == "error"), None)
    vec = {k: ps.get(k) for k in FIELDS}
    rec = {
        "run": i, "http": res["status"], "elapsed_s": res["elapsed"],
        "cache_cleared": {"pipeline_cache": n, "omp": m},
        "vector": vec,
        "gate_status": ver.get("gate_status"), "z3_status": ver.get("z3_status"),
        "unverified": ver.get("unverified"), "unverified_reason": ver.get("unverified_reason"),
        "error_frame": ({k: err.get(k) for k in ("error", "http_status", "reason")} if err else None),
        "verified_keys": sorted(ver.keys()) if ver else [],
        "full_provenance_stats": ps,
    }
    rows.append(rec)
    json.dump(ver, open("/tmp/phase_b_run%d_verified.json" % i, "w"), indent=1)
    draft = "".join(str(f.get("delta") or "") for f in res["frames"] if f.get("type") == "token")
    open("/tmp/phase_b_run%d_draft.txt" % i, "w").write(draft)
    import hashlib as _h
    rec["draft_sha"] = _h.sha256(draft.encode()).hexdigest()[:16]
    rec["draft_len"] = len(draft)
    print("\n--- run %d ---" % i)
    print("  draft sha=%s len=%d" % (rec["draft_sha"], rec["draft_len"]))
    print("  http=%s elapsed=%ss cache_cleared=%s" % (rec["http"], rec["elapsed_s"], rec["cache_cleared"]))
    print("  vector: %s" % json.dumps(vec))
    print("  full provenance_stats: %s" % json.dumps(ps))
    print("  gate_status=%s z3_status=%s unverified=%s" % (rec["gate_status"], rec["z3_status"], rec["unverified"]))
    if rec["unverified_reason"]:
        print("  unverified_reason: %s" % rec["unverified_reason"])
    if err:
        print("  ERROR FRAME: %s" % json.dumps(rec["error_frame"]))

print("\n=== distribution ===")
for k in FIELDS:
    vals = [r["vector"][k] for r in rows if isinstance(r["vector"].get(k), int)]
    if vals:
        print("  %-11s median=%s min=%s max=%s range=%s vals=%s" % (
            k, statistics.median(vals), min(vals), max(vals), max(vals) - min(vals), vals))
json.dump(rows, open("/tmp/phase_b_runs.json", "w"), indent=1)
print("\nwrote /tmp/phase_b_runs.json")
