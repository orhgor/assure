"""Compile probe — runs on the box, streams a draft, prints the frames.

usage: _probe_compile.py <project_id> <intent-file> [substrate_id ...]
Reads the entry-gate key from /etc/assure/shell-access.env (never printed) and calls the
public shell proxy on 127.0.0.1:8891, so every probe carries the gate key.
"""
import json, os, re, sys, time, urllib.request

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")


def gate_key() -> str:
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""


def post_stream(path, body, key, timeout=420.0):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Shell-Key": key}, method="POST")
    t0 = time.time(); frames = []; events = []; raw_lines = []; status = None
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
    return {"status": status, "elapsed": round(time.time() - t0, 2),
            "events": events, "frames": frames, "raw_lines": raw_lines}


def summarize(res):
    print(f"http {res['status']}  elapsed {res['elapsed']}s")
    print("events:", res["events"])
    for f in res["frames"]:
        t = f.get("type")
        if t in ("error", "complete"):
            print(f"  [{t}] {json.dumps({k: v for k, v in f.items() if k != 'document'})[:700]}")
        elif t == "token":
            print(f"  [token] {json.dumps({k: v for k, v in f.items() if k != 'text'})[:300]}")
        elif t == "usage":
            print(f"  [usage] {json.dumps(f)[:400]}")
        elif t == "verified":
            print(f"  [verified] keys={sorted(f.keys())}")
        elif t in ("compiled", "redhat", "status"):
            print(f"  [{t}] {json.dumps({k: v for k, v in f.items() if k not in ('prompt','compiled_prompt')})[:300]}")
    for f in res["frames"]:
        if "provenance_stats" in f:
            print("provenance_stats:", json.dumps(f["provenance_stats"]))
    for f in res["frames"]:
        if f.get("type") == "verified":
            doc = json.dumps(f.get("document") or f.get("document_body") or "")
            print("verified document (first 1200):", doc[:1200])
    for f in res["frames"]:
        if f.get("type") == "error":
            print("ERROR FRAME:", json.dumps(f)[:900])


if __name__ == "__main__":
    pid, intent_path = sys.argv[1], sys.argv[2]
    subs = sys.argv[3:]
    intent = open(intent_path).read().strip() if os.path.exists(intent_path) else intent_path
    res = post_stream(f"/api/projects/{pid}/draft/stream",
                      {"intent": intent, "substrate_file_ids": subs}, gate_key())
    summarize(res)
    draft = "".join(str(f.get("delta") or "") for f in res["frames"] if f.get("type") == "token")
    if draft:
        open("/tmp/last_draft.txt", "w").write(draft)
        print("draft chars:", len(draft), "-> /tmp/last_draft.txt")
    out = os.environ.get("PROBE_OUT")
    if out:
        json.dump({"project_id": pid, "intent": intent, "substrate_file_ids": subs, **res},
                  open(out, "w"), indent=1)
        print("wrote", out)
