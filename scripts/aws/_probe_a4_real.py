#!/usr/bin/env python3
"""A4: the anchored-ratio distribution — three REAL documents x three questions.

usage:
  SHELL_KEY=<gate key> _probe_a4_real.py ingest    # create projects, upload the PDFs
  SHELL_KEY=<gate key> _probe_a4_real.py measure   # nine compiles, one table

Nothing about which ratio is "good" is hardcoded here: the floor for A5 is
derived from this distribution, not the other way round. A refusal arrives as
HTTP 422 with no stats and is reported REFUSED, never as a 0% ratio.

Runs on the staging box against the shell gate on 127.0.0.1:8891, so the gate
key never leaves the box.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")
DOC_DIR = Path(os.environ.get("DOC_DIR", "/home/ubuntu/real-docs"))
STATE = DOC_DIR / "a4_state.json"


def _key() -> str:
    k = (os.environ.get("SHELL_KEY") or "").strip()
    if k:
        return k
    env = Path("/etc/assure/shell-access.env")
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("SHELL_ACCESS_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no SHELL_ACCESS_KEY: set SHELL_KEY or read /etc/assure/shell-access.env")


KEY = _key()
HEAD = {"X-Shell-Key": KEY, "User-Agent": "curl/8.7.1"}

DOCS = [
    {
        "id": "D1",
        "label": "IL DOI rate-filing decision letter (SERFF MHCI-134670754)",
        "file": "individual-mhci-134670754-py2026-decision-summary-final-sign.pdf",
        "url": "https://idoi.illinois.gov/content/dam/soi/en/web/insurance/consumers/documents/individual-mhci-134670754-py2026-decision-summary-final-sign.pdf",
        "questions": [
            ("specific", "What is the SERFF tracking number and the date the Department approved the filing?"),
            ("medium", "Summarize the Department's determination on this 2026 individual market rate filing."),
            ("broad", "How does the Department review individual market rate filings?"),
        ],
    },
    {
        "id": "D2",
        "label": "Commercial property policy (WV BRIM / Hallmark Specialty)",
        "file": "brim-cp-media371.pdf",
        "url": "https://brim.wv.gov/media/371/download?inline",
        "questions": [
            ("specific", "What is the policy number, the policy period and the total premium?"),
            ("medium", "Summarize the coverage parts and the schedule of policy attachments and forms."),
            ("broad", "How does commercial property insurance work?"),
        ],
    },
    {
        "id": "D3",
        "label": "ISO CP 10 30 09 17 Causes of Loss - Special Form",
        "file": "cp10300917-sample.pdf",
        "url": "https://ogs.ny.gov/system/files/documents/2021/09/cp10300917-sample.pdf",
        "questions": [
            ("specific", "What is the special limit for jewelry and watches?"),
            ("medium", "Summarize the exclusions in the Causes of Loss - Special Form."),
            ("broad", "What does commercial property insurance cover?"),
        ],
    },
]


def _post_json(path: str, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={**HEAD, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode("utf-8", "replace")[:400]}


def _get_json(path: str) -> tuple[int, dict]:
    req = urllib.request.Request(BASE + path, headers=HEAD)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode("utf-8", "replace")[:400]}


def _post_file(path: str, field: str, filename: str, data: bytes) -> tuple[int, dict]:
    boundary = "----a4" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + path,
        data=body,
        headers={**HEAD, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode("utf-8", "replace")[:600]}


def compile_one(pid: str, sid: str, intent: str) -> dict:
    body = json.dumps({"intent": intent, "substrate_file_ids": [sid]}).encode()
    req = urllib.request.Request(
        f"{BASE}/api/projects/{pid}/draft/stream",
        data=body,
        headers={**HEAD, "Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    t0 = time.time()
    frames: list[dict] = []
    status = None
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            status = resp.status
            for line in resp:
                text = line.decode("utf-8", "replace").rstrip("\n")
                if text.startswith("data: "):
                    try:
                        frames.append(json.loads(text[6:]))
                    except Exception:
                        pass
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            frames.append(json.loads(e.read().decode("utf-8", "replace")))
        except Exception:
            pass
    stats, err = None, None
    for f in frames:
        if f.get("provenance_stats") is not None and stats is None:
            stats = f["provenance_stats"]
        if f.get("type") == "error":
            err = f.get("reason") or f.get("error")
        if f.get("type") == "complete" and f.get("ok") is False and err is None:
            err = f.get("error")
    return {"status": status, "elapsed": round(time.time() - t0, 1), "stats": stats, "error": err}


def do_ingest() -> None:
    state = {}
    for doc in DOCS:
        pdf = DOC_DIR / doc["file"]
        if not pdf.exists():
            print(f"{doc['id']}: MISSING {pdf}")
            continue
        raw = pdf.read_bytes()
        status, created = _post_json("/api/projects", {"title": f"A4 {doc['id']} {int(time.time())}"})
        pid = (created or {}).get("id")
        print(f"\n=== {doc['id']} {doc['file']} ({len(raw)} bytes) project={pid} ({status}) ===", flush=True)
        if not pid:
            print("   project creation failed:", str(created)[:300])
            continue
        st, res = _post_file(f"/api/projects/{pid}/jdf/ingest", "file", doc["file"], raw)
        print(f"   ingest http={st} ok={res.get('ok')} chunks={res.get('chunks_stored')} "
              f"pages={res.get('page_count')} err={str(res.get('error') or res.get('raw') or '')[:200]}", flush=True)
        st2, subs = _get_json(f"/api/projects/{pid}/substrate")
        entries = subs.get("entries") or subs.get("files") or []
        sid = None
        for e in entries:
            if str(e.get("filename") or "") == doc["file"]:
                sid = e.get("id")
        if sid is None and entries:
            sid = entries[0].get("id")
        chars = 0
        for e in entries:
            if e.get("id") == sid:
                chars = len(str(e.get("extracted_text") or ""))
        print(f"   substrate id={sid} extracted_chars={chars} entries={len(entries)}", flush=True)
        state[doc["id"]] = {"pid": pid, "sid": sid, "label": doc["label"],
                            "file": doc["file"], "url": doc["url"], "chars": chars,
                            "questions": doc["questions"]}
    STATE.write_text(json.dumps(state, indent=2))
    print(f"\nstate -> {STATE}")


# The two broad asks the first round refused on ``opening_token_ungrounded`` —
# a rule that runs before the ratio and has nothing to do with it. The refusal
# message tells the user to make the ask more specific, so these are that
# instruction followed: same document, same breadth, a concrete ask.
ROUND2 = {
        "D1": ("broad", "What does the Department say about its process and considerations for the 2026 plan year?"),
        "D2": ("broad", "What does the policy cover for the State of West Virginia, and over what period?"),
    }


def do_measure(round2: bool = False) -> None:
    state = json.loads(STATE.read_text())
    rows = []
    for did, doc in state.items():
        questions = [ROUND2[did]] if (round2 and did in ROUND2) else (
            [] if round2 else doc["questions"]
        )
        print(f"\n=== {did} {doc['label']} [{doc['pid']}] chars={doc['chars']} ===", flush=True)
        for kind, q in questions:
            r = compile_one(doc["pid"], doc["sid"], q)
            s = r["stats"] or {}
            elig, anch = s.get("eligible"), s.get("anchored")
            if r["status"] == 422 or r["error"]:
                print(f"  {kind:<9} REFUSED http={r['status']} ({r['elapsed']}s) "
                      f"reason={str(r['error'])[:80]}", flush=True)
                rows.append([did, kind, q, None, None, None, "REFUSED"])
                continue
            if not elig:
                print(f"  {kind:<9} NO-STATS http={r['status']} ({r['elapsed']}s) "
                      f"stats={str(s)[:120]}", flush=True)
                rows.append([did, kind, q, elig, anch, None, "NO-STATS"])
                continue
            ratio = anch / elig * 100.0
            print(f"  {kind:<9} eligible={elig} anchored={anch} supported={s.get('supported')} "
                  f"partial={s.get('partial')} unsupported={s.get('unsupported')} "
                  f"unanchored={s.get('unanchored')} ratio={ratio:.0f}% ({r['elapsed']}s)", flush=True)
            rows.append([did, kind, q, elig, anch, ratio, "ok"])

    print("\n\n=== A4 DISTRIBUTION (anchored / eligible) ===")
    print(f"{'doc':<4} {'ask':<9} {'elig':>5} {'anch':>5} {'ratio':>7}  question")
    vals = []
    for did, kind, q, elig, anch, ratio, st in rows:
        if st == "ok":
            print(f"{did:<4} {kind:<9} {elig:>5} {anch:>5} {ratio:>6.0f}%  {q[:60]}")
            vals.append(ratio)
        else:
            print(f"{did:<4} {kind:<9} {'-':>5} {'-':>5} {st:>7}  {q[:60]}")
    if vals:
        v = sorted(vals)
        n = len(v)
        med = v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2
        print(f"\ndata points={n}  min={min(v):.0f}%  median={med:.0f}%  max={max(v):.0f}%")
        print("sorted:", [f"{x:.0f}%" for x in v])
    print("\nraw rows:", json.dumps(rows, ensure_ascii=False))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "measure"
    if mode == "ingest":
        do_ingest()
    elif mode == "measure":
        do_measure()
    elif mode == "measure2":
        do_measure(round2=True)
    else:
        raise SystemExit(f"unknown mode {mode!r} (ingest|measure|measure2)")
