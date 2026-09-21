"""Compile probe — runs on the box, streams a draft, prints the frames.

usage: _compile_probe.py <project_id> <intent-file-or-literal> [substrate_id ...]

Reads the entry-gate key from /etc/assure/shell-access.env (never printed) and goes
through the public shell proxy on 127.0.0.1:8891 so every probe carries the gate key.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")


def gate_key() -> str:
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""


def post_stream(path: str, body: dict, key: str, timeout: float = 300.0) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Shell-Key": key},
        method="POST",
    )
    t0 = time.time()
    frames: list[dict] = []
    events: list[str] = []
    raw_lines: list[str] = []
    status = None
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = resp.status
        for line in resp:
            text = line.decode("utf-8", "replace").rstrip("\n")
            raw_lines.append(text)
            if text.startswith("event: "):
                events.append(text[7:].strip())
            elif text.startswith("data: "):
                try:
                    frames.append(json.loads(text[6:]))
                except Exception:
                    pass
    return {
        "status": status,
        "elapsed": round(time.time() - t0, 2),
        "events": events,
        "frames": frames,
        "raw_lines": raw_lines,
    }


def summarize(res: dict) -> None:
    print(f"http {res['status']}  elapsed {res['elapsed']}s")
    print("events:", res["events"])
    for f in res["frames"]:
        t = f.get("type")
        if t in ("error", "complete"):
            print(f"  [{t}] {json.dumps({k: v for k, v in f.items() if k != 'document'})[:600]}")
        elif t == "verified":
            print(f"  [verified] keys={sorted(f.keys())}")
        elif t == "token":
            print(f"  [token] {json.dumps({k: v for k, v in f.items() if k != 'text'})[:300]}")
        elif t == "usage":
            print(f"  [usage] {json.dumps(f)[:400]}")
        elif t == "compiled":
            print(f"  [compiled] keys={sorted(f.keys())} prompt_chars={len(str(f.get('prompt') or f.get('compiled_prompt') or ''))}")
        elif t == "status":
            print(f"  [status] {json.dumps(f)[:200]}")
        elif t == "redhat":
            print(f"  [redhat] {json.dumps(f)[:200]}")
    # provenance + document
    for f in res["frames"]:
        if "provenance_stats" in f:
            print("provenance_stats:", json.dumps(f["provenance_stats"]))
    for f in res["frames"]:
        if f.get("type") == "verified":
            doc = json.dumps(f.get("document") or f.get("document_body") or "")
            print("verified document (first 900):", doc[:900])
    for f in res["frames"]:
        if f.get("type") == "error":
            print("ERROR FRAME:", json.dumps(f)[:800])


if __name__ == "__main__":
    pid = sys.argv[1]
    intent_arg = sys.argv[2]
    subs = sys.argv[3:]
    intent = intent_arg
    if os.path.exists(intent_arg):
        intent = open(intent_arg).read().strip()
    key = gate_key()
    res = post_stream(
        f"/api/projects/{pid}/draft/stream",
        {"intent": intent, "substrate_file_ids": subs},
        key,
    )
    summarize(res)
    out = os.environ.get("PROBE_OUT")
    if out:
        with open(out, "w") as fh:
            json.dump({"intent": intent, "substrate_file_ids": subs, **res}, fh, indent=1)
        print("wrote", out)
