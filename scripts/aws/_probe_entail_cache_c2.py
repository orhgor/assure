"""C2 — prove the entailment cache is live through two genuine compiles.

The compile route replays a cached tree unless the AST cache is cleared, so both
compiles clear it first and are real pipeline runs. Each compile's entailment pass
judges its own anchored paragraphs; a paragraph the model re-states with the same
claim against the same anchor window is a cache hit and costs no model call.

Reports, per compile: anchored paragraphs, new `pipeline_cache` kind='entailment'
rows (one per miss), and the cache hits the service logged.

usage: _probe_entail_cache_c2.py [runs]
"""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")
PROJECT = "demo-3235f5"
SOURCE_ID = "sub-d3eab1f0fa9c486d"
INTENT_FILE = "/home/ubuntu/probes/demo_intent.txt"
OMP_DB = "/home/ubuntu/.omp/omp.db"
RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 2


def gate_key():
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""


def db():
    return sqlite3.connect("prompt_matrix/history.sqlite")


def clear_ast_cache(pid):
    app = db()
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


def ent_rows(pid):
    app = db()
    rows = app.execute(
        "select cache_key, updated_at from pipeline_cache where project_id=? and kind='entailment'"
        " order by updated_at desc", (pid,)).fetchall()
    app.close()
    return rows


def journal_hits(since_iso):
    """Lines the service logged for a cache hit since an ISO timestamp."""
    import subprocess
    try:
        out = subprocess.run(
            ["journalctl", "-u", "assure-prototype.service", "--since", since_iso, "--no-pager"],
            capture_output=True, text=True, timeout=60).stdout
    except Exception as exc:
        return ["journalctl failed: %s" % exc]
    return [ln for ln in out.splitlines() if "entailment-cache" in ln]


def post_stream(path, body, key, timeout=600.0):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Shell-Key": key}, method="POST")
    t0 = time.time()
    frames = []
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for line in resp:
            text = line.decode("utf-8", "replace").rstrip("\n")
            if text.startswith("data: "):
                try:
                    frames.append(json.loads(text[6:]))
                except Exception:
                    pass
    return {"elapsed": round(time.time() - t0, 2), "frames": frames}


def judged(frames):
    """(claim, window, verdict, checked_at) for each paragraph carrying a verdict."""
    out = []
    for f in frames:
        if not isinstance(f, dict) or not isinstance(f.get("document"), dict):
            continue
        def walk(n):
            if not isinstance(n, dict):
                return
            prov = n.get("provenance")
            provs = prov if isinstance(prov, list) else ([prov] if prov else [])
            for p in provs:
                if not isinstance(p, dict):
                    continue
                ent = p.get("entailment")
                if isinstance(ent, dict) and ent.get("verdict"):
                    out.append({
                        "claim": " ".join(str(n.get("content") or "").split())[:70],
                        "window": " ".join(str(p.get("anchor_window") or p.get("extracted_quote") or "").split())[:70],
                        "verdict": ent.get("verdict"),
                        "checked_at": ent.get("checked_at"),
                        "model": ent.get("model"),
                    })
            for c in (n.get("children") or []):
                walk(c)
        walk(f.get("document"))
        if out:
            break
    return out


intent = open(INTENT_FILE).read().strip()
print("project=%s source=%s intent_sha=%s" % (
    PROJECT, SOURCE_ID, __import__("hashlib").sha256(intent.encode()).hexdigest()))
rows0 = ent_rows(PROJECT)
print("entailment cache rows BEFORE: %d" % len(rows0))

for i in range(1, RUNS + 1):
    since = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 5))
    n, m = clear_ast_cache(PROJECT)
    before = len(ent_rows(PROJECT))
    res = post_stream(f"/api/projects/{PROJECT}/draft/stream",
                      {"intent": intent, "compileType": "full", "substrate_file_ids": [SOURCE_ID]},
                      gate_key())
    after = len(ent_rows(PROJECT))
    hits = journal_hits(since)
    jd = judged(res["frames"])
    print("\n--- compile %d ---" % i)
    print("  elapsed=%ss ast_cleared=%s" % (res["elapsed"], {"pipeline_cache": n, "omp": m}))
    print("  entailment rows: before=%d after=%d -> new (misses/calls)=%d" % (before, after, after - before))
    print("  cache-hit log lines: %d" % len(hits))
    for h in hits[:8]:
        print("     %s" % h.strip()[-120:])
    for j in jd:
        print("  judged  %-9s checked_at=%s  %s" % (j["verdict"], j["checked_at"], j["claim"]))
    print("  anchored paragraphs judged: %d" % len(jd))
    rows = ent_rows(PROJECT)
    print("  total entailment rows now: %d" % len(rows))
    if i == RUNS:
        print("  sample stored rows:")
        for k, u in rows[:3]:
            print("     %s  %s" % (u, k))
