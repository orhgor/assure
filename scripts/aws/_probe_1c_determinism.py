#!/usr/bin/env python3
"""1C — determinism: N identical draft hashes on a real document.

usage: _probe_1c_determinism.py [runs] [project_id] [source_id]

The same intent against the same source, compiled `runs` times, with BOTH caches
cleared before every run:

  * `pipeline_cache` (kind='ast') in the app DB — without this, run 2..5 would
    replay run 1's answer and "identical hashes" would prove only that the cache
    works;
  * the omp memories tagged `ast:<project>:…`.

Against a real ingested document. `project_id`/`source_id` default to document 1
(`a4-d3-1789759434-4a6346`), but a copy project is preferred so the measured
document's revision pointer stops moving: the compile path commits a revision, so
every run of this probe writes one.

Prints, per run: the sha256 and length of the draft, the model the pipeline
reported, and the provenance vector. The full draft of every run is written to
/home/ubuntu/probes/1c_draft_<n>.txt, so a differing run can be diffed rather
than guessed at. Frame keys of run 1 are dumped once: if a provider or generation
id ever reaches the client, that is where it would be.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")
APP_DB = os.environ.get("ASSURE_DB", "/home/ubuntu/assure-prototype/prompt_matrix/history.sqlite")
OMP_DB = os.environ.get("OMP_DB", "/home/ubuntu/.omp/omp.db")
PROJECT = os.environ.get("PROBE_PROJECT", "a4-d3-1789759434-4a6346")
SOURCE_ID = os.environ.get("PROBE_SOURCE", "sub-462c18f240614845")
INTENT = os.environ.get(
    "PROBE_INTENT",
    "Summarize the exclusions in the Causes of Loss - Special Form.",
)
RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 5
if len(sys.argv) > 2:
    PROJECT = sys.argv[2]
if len(sys.argv) > 3:
    SOURCE_ID = sys.argv[3]
DRAFT_DIR = Path(os.environ.get("PROBE_DRAFT_DIR", "/home/ubuntu/probes"))
FIELDS = ("eligible", "anchored", "supported", "partial", "unsupported", "unanchored")


def gate_key() -> str:
    if os.environ.get("SHELL_KEY"):
        return os.environ["SHELL_KEY"]
    raw = Path("/etc/assure/shell-access.env").read_text()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""


KEY = gate_key()


def clear(pid: str) -> tuple[int, int]:
    app = sqlite3.connect(APP_DB)
    n = app.execute(
        "delete from pipeline_cache where project_id=? and kind='ast'", (pid,)
    ).rowcount
    app.commit()
    app.close()
    m = 0
    try:
        omp = sqlite3.connect(OMP_DB)
        rows = omp.execute(
            "select rowid from memories where tags like ?", (f'%"ast:{pid}:%',)
        ).fetchall()
        for r in rows:
            omp.execute("delete from memories where rowid=?", (r[0],))
        omp.commit()
        omp.close()
        m = len(rows)
    except Exception as exc:  # the omp store is a second cache, not the gate
        print("  [omp warn]", exc)
    return n, m


def post(path: str, body: dict, timeout: float = 900.0) -> list[dict]:
    """POST the SSE endpoint once. Retries only transport/5xx failures: a restart
    mid-probe must not be read as a nondeterministic compile."""
    last = None
    for attempt in range(1, 6):
        try:
            req = urllib.request.Request(
                BASE + path,
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "X-Shell-Key": KEY,
                         "User-Agent": "curl/8.7.1", "Accept": "text/event-stream"},
                method="POST",
            )
            frames: list[dict] = []
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                for line in resp:
                    text = line.decode("utf-8", "replace").rstrip("\n")
                    if text.startswith("data: "):
                        try:
                            frames.append(json.loads(text[6:]))
                        except Exception:
                            pass
            if not frames:
                raise RuntimeError("empty stream")
            return frames
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code < 500:
                raise
        except Exception as exc:  # URLError, ConnectionRefused, empty stream
            last = exc
        print(f"  [retry {attempt}/5] {type(last).__name__}: {last}")
        time.sleep(15)
    raise RuntimeError(f"endpoint unreachable after 5 attempts: {last}")


def main() -> None:
    print(f"project={PROJECT} source={SOURCE_ID} runs={RUNS}")
    print("intent sha256:", hashlib.sha256(INTENT.encode()).hexdigest())
    print("intent:", INTENT[:90])
    rows = []
    for i in range(1, RUNS + 1):
        cleared, omp_cleared = clear(PROJECT)
        t0 = time.time()
        frames = post(
            f"/api/projects/{PROJECT}/draft/stream",
            {"intent": INTENT, "compileType": "full", "substrate_file_ids": [SOURCE_ID]},
        )
        draft, model, stats, err, cached = "", "", {}, None, False
        rid, mid = "", ""
        for f in frames:
            if f.get("draft_text") and not draft:
                draft = str(f["draft_text"])
            if f.get("cache_hit"):
                cached = True
            if f.get("model") and not model:
                model = str(f["model"])
            if f.get("request_id") and not rid:
                rid = str(f["request_id"])
            if f.get("model_id") and not mid:
                mid = str(f["model_id"])
            if f.get("provenance_stats"):
                stats = f["provenance_stats"]
            if f.get("type") == "error":
                err = f.get("reason") or f.get("error")
        if i == 1:
            keys = sorted({k for f in frames for k in f})
            print("frame keys seen:", keys)
        digest = hashlib.sha256(draft.encode()).hexdigest() if draft else "(no draft)"
        paragraphs = len([p for p in re.split(r"\n\s*\n", draft) if p.strip()])
        (DRAFT_DIR / f"1c_draft_{i}.txt").write_text(draft)
        vector = " ".join(f"{k}={stats.get(k)}" for k in FIELDS if stats.get(k) is not None)
        rows.append((i, digest, len(draft), cached, model, vector, err))
        print(f"\nrun {i}: cleared pipeline_cache={cleared} omp={omp_cleared} "
              f"({round(time.time() - t0, 1)}s) cache_hit={cached}")
        print(f"  draft sha256={digest} len={len(draft)} blocks={paragraphs}")
        print(f"  model={model or '(none reported)'}  reason={err}")
        print(f"  {vector}")

    hashes = [r[1] for r in rows if r[1] != "(no draft)"]
    missing = [r[0] for r in rows if r[1] == "(no draft)"]
    unique = sorted(set(hashes))
    print("\n=== 1C RESULT ===")
    for i, digest, length, cached, model, vector, err in rows:
        print(f"  run {i}: {digest} len={length} cache_hit={cached} {vector}")
    print(f"\ndistinct draft hashes: {len(unique)} of {len(hashes)} runs that produced a draft")
    if missing:
        print(f"runs that produced NO draft (a restart mid-run, not a model reading): {missing}")
    if len(unique) > 1:
        base = max(set(hashes), key=hashes.count)
        print(f"majority hash: {base} ({hashes.count(base)}/{len(hashes)})")
        for i, digest, *_ in rows:
            if digest != base:
                print(f"  OUTLIER run {i}: {digest} — see {DRAFT_DIR}/1c_draft_{i}.txt")
    print("VERDICT:", f"PASS — {len(hashes)} identical draft hashes"
          + (f" ({len(missing)} run(s) lost to a restart)" if missing else "")
          if len(unique) == 1 and len(hashes) >= 5
          else f"FAIL — {len(unique)} distinct drafts in {len(hashes)} runs")


if __name__ == "__main__":
    main()
