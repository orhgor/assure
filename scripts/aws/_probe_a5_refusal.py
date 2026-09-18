#!/usr/bin/env python3
"""A5 proof: the ratio-floor refusal fires end to end, and persists nothing.

usage: _probe_a5_refusal.py <project_id> <substrate_id> "<ask>" ["<ask>" ...]

For each ask: post the compile, capture the refusal frame (reason + the
client-facing message) or the provenance counters, then re-read the project's
persisted state — ``jdf_revisions`` count, ``jdf_documents`` updated_at and
``projects.current_version`` — and print the delta. A refusal that wrote
anything shows up as a non-zero delta.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")
DB = os.environ.get("ASSURE_DB", "/home/ubuntu/assure-prototype/prompt_matrix/history.sqlite")
# The probe runs from the data directory; the guard it reports on lives in the
# checkout beside it.
sys.path.insert(0, os.environ.get("ASSURE_REPO", "/home/ubuntu/assure-prototype"))


def key() -> str:
    if os.environ.get("SHELL_KEY"):
        return os.environ["SHELL_KEY"]
    for line in Path("/etc/assure/shell-access.env").read_text().splitlines():
        if line.startswith("SHELL_ACCESS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no key")


HEAD = {"X-Shell-Key": key(), "User-Agent": "curl/8.7.1"}


def state(pid: str) -> dict:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rev = c.execute("select count(*) from jdf_revisions where project_id=?", (pid,)).fetchone()[0]
    doc = c.execute("select updated_at from jdf_documents where project_id=?", (pid,)).fetchone()
    ver = c.execute("select current_version from projects where id=?", (pid,)).fetchone()
    maxrev = c.execute("select max(version) from jdf_revisions where project_id=?", (pid,)).fetchone()[0]
    c.close()
    return {"revisions": rev, "max_version": maxrev, "doc_updated_at": doc[0] if doc else None,
            "current_version": ver[0] if ver else None}


def compile_one(pid: str, sid: str, intent: str) -> dict:
    body = json.dumps({"intent": intent, "substrate_file_ids": [sid]}).encode()
    req = urllib.request.Request(
        f"{BASE}/api/projects/{pid}/draft/stream", data=body,
        headers={**HEAD, "Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST")
    t0 = time.time()
    frames, status, err, stats, msg, draft = [], None, None, None, None, ""
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            status = resp.status
            for line in resp:
                t = line.decode("utf-8", "replace").rstrip("\n")
                if t.startswith("data: "):
                    try:
                        f = json.loads(t[6:])
                    except Exception:
                        continue
                    frames.append(f)
                    if f.get("provenance_stats") and stats is None:
                        stats = f["provenance_stats"]
                    if f.get("draft_text") and not draft:
                        draft = str(f["draft_text"])
                    if f.get("type") == "error":
                        err = f.get("reason") or f.get("error")
                        msg = f.get("error")
    except urllib.error.HTTPError as e:
        status = e.code
    return {"status": status, "elapsed": round(time.time() - t0, 1), "stats": stats,
            "reason": err, "message": msg, "frames": len(frames), "draft": draft}


def main() -> None:
    pid, sid = sys.argv[1], sys.argv[2]
    asks = sys.argv[3:]
    for ask in asks:
        before = state(pid)
        r = compile_one(pid, sid, ask)
        after = state(pid)
        delta = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
        s = r["stats"] or {}
        print(f"\nASK: {ask}")
        print(f"  http={r['status']} frames={r['frames']} {r['elapsed']}s")
        if s:
            print(f"  stats eligible={s.get('eligible')} anchored={s.get('anchored')} "
                  f"unanchored={s.get('unanchored')} "
                  f"ratio={(s.get('anchored',0)/s.get('eligible',1)*100 if s.get('eligible') else 0):.0f}%")
        print(f"  reason={r['reason']}")
        print(f"  message={r['message']}")
        if r.get("draft"):
            print(f"  draft opens: {r['draft'][:150]!r}")
            try:
                from prompt_matrix.services.compile_guard import (
                    first_sentence, is_question_to_source_bridge)
                fs = first_sentence(r["draft"])
                print(f"  first_sentence={fs[:120]!r}")
                print(f"  bridged(question-to-source)? {is_question_to_source_bridge(fs)}")
            except Exception as exc:
                print(f"  (bridge check unavailable: {exc})")
        print(f"  persisted before={before}")
        print(f"  persisted after ={after}")
        print(f"  DELTA={'NONE — nothing persisted' if not delta else delta}")


if __name__ == "__main__":
    main()
