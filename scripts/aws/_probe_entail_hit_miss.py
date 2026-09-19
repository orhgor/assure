"""C2 — one hit whose raw output equals the cached verdict, and one miss (a fresh call).

Runs in the app's own venv on the box:

  1. compiles the demo intent once (genuine: the AST cache is cleared first) and
     reads the (claim, evidence) pair of every anchored paragraph out of the
     returned document with the pipeline's own `_claim_source`;
  2. re-judges each pair through `check_entailment` — the single place a verdict
     is obtained — and prints the raw record, with the `token_ledger_entries`
     count before/after as the model-call counter: a hit must add zero rows and
     return a record identical to the stored one;
  3. edits ONE paragraph's claim and judges it again: a new key, so a real call,
     a fresh `checked_at`, and exactly one new ledger row.

usage: _probe_entail_hit_miss.py
"""
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request

sys.path.insert(0, "/home/ubuntu/assure-prototype")

from prompt_matrix.services.entailment import (  # noqa: E402
    _claim_source,
    build_entailment_prompt,
    check_entailment,
)
from prompt_matrix.services.entailment_cache import verdict_cache_key  # noqa: E402

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")
PROJECT = "demo-3235f5"
SOURCE_ID = "sub-d3eab1f0fa9c486d"
INTENT_FILE = "/home/ubuntu/probes/demo_intent.txt"
OMP_DB = "/home/ubuntu/.omp/omp.db"
APP_DB = "prompt_matrix/history.sqlite"


def gate_key():
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""


def ledger_count():
    c = sqlite3.connect(APP_DB)
    n = c.execute("select count(*) from token_ledger_entries where task_type='semantic_validation'").fetchone()[0]
    c.close()
    return n


def cached_payload(key):
    c = sqlite3.connect(APP_DB)
    r = c.execute("select payload_json, updated_at from pipeline_cache where cache_key=?", (key,)).fetchone()
    c.close()
    return (json.loads(r[0]), r[1]) if r else (None, None)


def clear_ast_cache(pid):
    app = sqlite3.connect(APP_DB)
    n = app.execute("delete from pipeline_cache where project_id=? and kind='ast'", (pid,)).rowcount
    app.commit()
    app.close()
    try:
        omp = sqlite3.connect(OMP_DB)
        rows = omp.execute("select rowid from memories where tags like ?", (f'%"ast:{pid}:%',)).fetchall()
        for r in rows:
            omp.execute("delete from memories where rowid=?", (r[0],))
        omp.commit()
        omp.close()
    except Exception:
        pass
    return n


def compile_once(intent):
    req = urllib.request.Request(
        BASE + f"/api/projects/{PROJECT}/draft/stream",
        data=json.dumps({"intent": intent, "compileType": "full",
                         "substrate_file_ids": [SOURCE_ID]}).encode(),
        headers={"Content-Type": "application/json", "X-Shell-Key": gate_key()}, method="POST")
    frames = []
    with urllib.request.urlopen(req, timeout=600) as resp:
        for line in resp:
            t = line.decode("utf-8", "replace").rstrip("\n")
            if t.startswith("data: "):
                try:
                    frames.append(json.loads(t[6:]))
                except Exception:
                    pass
    return frames


def anchored_pairs(frames):
    """[(node_id, claim, evidence)] for each anchored paragraph, via the pipeline's own split.

    Mirrors `attach_entailment_to_tree`'s traversal exactly: body -> section ->
    (section itself, section children), paragraph nodes only.
    """
    out = []
    for f in frames:
        if not isinstance(f, dict) or f.get("type") != "verified":
            continue
        document = f.get("document")
        if not isinstance(document, dict):
            continue
        for section in document.get("body") or []:
            if not isinstance(section, dict):
                continue
            for node in [section, *(section.get("children") or [])]:
                if not isinstance(node, dict) or str(node.get("type") or "") != "paragraph":
                    continue
                claim, ev = _claim_source(node)
                if claim and ev:
                    ent = ((node.get("meta") or {}).get("provenance") or {}).get("entailment")
                    out.append((node.get("id"), claim, ev, ent))
    return out


intent = open(INTENT_FILE).read().strip()
print("intent_sha=%s" % hashlib.sha256(intent.encode()).hexdigest())
print("ast cache rows cleared before the compile: %d" % clear_ast_cache(PROJECT))

l0 = ledger_count()
frames = compile_once(intent)
l1 = ledger_count()
print("compile done: semantic_validation ledger rows %d -> %d (%d entailment calls)" % (l0, l1, l1 - l0))

pairs = anchored_pairs(frames)
print("anchored paragraphs in the compiled document: %d" % len(pairs))

print("\n=== PASS 1 — re-judge the same paragraphs (expected: all hits) ===")
hit_proof = None
for nid, claim, ev, ent_at_compile in pairs:
    before = ledger_count()
    rec = check_entailment(claim, ev, project_id=PROJECT)
    after = ledger_count()
    key = verdict_cache_key(claim, ev, build_entailment_prompt(claim, ev), rec.get("model") or "")
    stored, updated = cached_payload(key)
    stored_record = (stored or {}).get("record")
    same_as_stored = stored_record == rec
    print("\n  node=%s ledger %d -> %d (%s)" % (
        nid, before, after, "HIT (no call)" if after == before else "MISS (call made)"))
    print("    key          : %s" % key)
    print("    claim        : %s" % claim[:110])
    print("    verdict at compile : %s" % json.dumps(ent_at_compile))
    print("    RAW RETURNED : %s" % json.dumps(rec))
    print("    RAW STORED   : %s  (row updated_at=%s)" % (json.dumps(stored_record), updated))
    print("    returned == stored : %s   verdict at compile == returned : %s" % (
        same_as_stored, ent_at_compile == rec))
    if after == before and same_as_stored and hit_proof is None:
        hit_proof = {"node": nid, "key": key, "record": rec}

print("\n=== PASS 2 — edit ONE paragraph's claim (expected: one miss, a fresh call) ===")
nid, claim, ev, _ent = pairs[0]
edited = claim + " The deductible is 9 percent for all coastal counties."
before = ledger_count()
rec2 = check_entailment(edited, ev, project_id=PROJECT)
after = ledger_count()
print("  node=%s ledger %d -> %d (%s)" % (
    nid, before, after, "MISS (call made)" if after > before else "HIT"))
print("  edited claim : %s" % edited[:140])
print("  RAW RETURNED : %s" % json.dumps(rec2))
print("  edited key   : %s" % verdict_cache_key(edited, ev, build_entailment_prompt(edited, ev), rec2.get("model") or ""))
print("  original key : %s" % verdict_cache_key(claim, ev, build_entailment_prompt(claim, ev), rec2.get("model") or ""))
again = check_entailment(edited, ev, project_id=PROJECT)
after2 = ledger_count()
print("  re-judging the edited claim: ledger %d -> %d (%s); same record: %s" % (
    after, after2, "HIT (no call)" if after2 == after else "MISS", again == rec2))

c = sqlite3.connect(APP_DB)
print("\nentailment cache rows for %s: %d" % (
    PROJECT, c.execute("select count(*) from pipeline_cache where project_id=? and kind='entailment'", (PROJECT,)).fetchone()[0]))
c.close()
