"""Live check of the compile with no source attached — Phase A of the no-source
ticket, observed server-side.

A scratch project with zero sources is asked to compile the demo intent, and the
response is read line by line with the elapsed time each line arrived, so the
question "where does the pipeline halt" is answered by the stream itself: which
frames arrived, whether a terminal frame ever did, and what the HTTP status was.

Persistence is read back immediately afterwards (revisions, max version, project
row, refusal audit rows) — "nothing was saved" is a claim about those numbers.

usage:
  _probe_no_source_compile.py                 # create + compile + snapshot
  _probe_no_source_compile.py --delete <pid>  # delete the scratch project

The application database is opened read-only and nothing in prompt_matrix is
touched. The gate key never leaves this process.
"""

import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")
DB = os.environ.get(
    "PROBE_DB", "/home/ubuntu/assure-prototype/prompt_matrix/history.sqlite"
)
NONCE = "%d-%s" % (int(time.time()), uuid.uuid4().hex[:6])
TITLE = os.environ.get("PROBE_TITLE", "phase-a-no-source-" + NONCE)
INTENT = (
    "write me about human physiology and physiological arousals within the "
    "context of mental health"
)


def gate_key() -> str:
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""


KEY = gate_key()


def request(path, *, body=None, ctype=None, timeout=600.0):
    headers = {"X-Shell-Key": KEY}
    if ctype:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(BASE + path, data=body, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    return resp.status, resp.read().decode("utf-8", "replace")


def snap(project_id):
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT COUNT(*) AS n FROM jdf_revisions WHERE project_id=?", (project_id,)
        ).fetchone()
        proj = con.execute(
            "SELECT title, current_version, updated_at FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
        return {
            "revision_count": int(row["n"]),
            "project": dict(proj) if proj else None,
            "audit_rows": con.execute(
                "SELECT COUNT(*) FROM audit_log WHERE project_id=?", (project_id,)
            ).fetchone()[0],
            "refusal_audit_rows": con.execute(
                "SELECT COUNT(*) FROM audit_log WHERE project_id=? AND "
                "error_message LIKE 'compile refused:%'",
                (project_id,),
            ).fetchone()[0],
            "compiled_json": (dict(proj).get("last_compiled_json")
                              if proj else None),
        }
    finally:
        con.close()


def compile_stream(project_id, *, intent, source_ids):
    """Stream the compile, timestamping every line. Returns the raw trace."""
    payload = {"intent": intent, "substrate_file_ids": source_ids, "compileType": "full"}
    req = urllib.request.Request(
        BASE + "/api/projects/%s/draft/stream" % project_id,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Shell-Key": KEY},
        method="POST",
    )
    t0 = time.time()
    trace, frames = [], []
    status = None
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            status = resp.status
            ctype = resp.headers.get("Content-Type")
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\n")
                t = round(time.time() - t0, 3)
                if line.strip():
                    trace.append((t, line))
                if line.startswith("data: "):
                    body = line[6:].strip()
                    if body == "[DONE]":
                        frames.append({"type": "done"})
                    else:
                        try:
                            frames.append(json.loads(body))
                        except ValueError:
                            pass
    except urllib.error.HTTPError as exc:
        status = exc.code
        trace.append((round(time.time() - t0, 3), "HTTPError %s: %s" % (exc.code, exc.read()[:400])))
        ctype = None
    return {
        "http_status": status,
        "content_type": ctype,
        "elapsed": round(time.time() - t0, 3),
        "trace": trace,
        "frames": frames,
    }


def token_chars(frames) -> int:
    return sum(len(str(f.get("delta") or "")) for f in frames if f.get("type") == "token")


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--delete":
        pid = sys.argv[2]
        req = urllib.request.Request(
            BASE + "/api/projects/" + pid,
            headers={"X-Shell-Key": KEY},
            method="DELETE",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=60)
            print("[delete] http %s %s" % (resp.status, resp.read().decode()[:200]))
        except urllib.error.HTTPError as exc:
            print("[delete] http %s %s" % (exc.code, exc.read().decode()[:200]))
        return

    status, created = request(
        "/api/projects", body=json.dumps({"title": TITLE}).encode(),
        ctype="application/json",
    )
    try:
        payload = json.loads(created)
    except ValueError:
        payload = {}
    pid = (payload.get("project") or {}).get("id") or payload.get("id")
    print("[project] http %s id=%s title=%r" % (status, pid, TITLE))
    if not pid:
        print("[project] create failed:", created[:400])
        return

    # Prove the premise: this project has no sources attached to it.
    sub_status, sub_body = request("/api/projects/%s/substrate" % pid)
    try:
        subs = json.loads(sub_body)
    except ValueError:
        subs = {"_raw": sub_body[:200]}
    print("[sources] http %s files=%s" % (sub_status, json.dumps(subs)[:300]))

    before = snap(pid)
    print("[before] revisions=%s audit_rows=%s refusal_audit_rows=%s"
          % (before["revision_count"], before["audit_rows"], before["refusal_audit_rows"]))

    print("\n[intent] %s" % INTENT)
    res = compile_stream(pid, intent=INTENT, source_ids=[])
    print("[http] status=%s content_type=%s elapsed=%ss lines=%d frames=%d token_chars=%d"
          % (res["http_status"], res["content_type"], res["elapsed"],
             len(res["trace"]), len(res["frames"]), token_chars(res["frames"])))

    print("\n--- raw SSE trace (elapsed, line) ---")
    for t, line in res["trace"]:
        print("%8.3fs  %s" % (t, line[:600]))
    print("--- end trace (last line above is the end of the response) ---")

    print("\n--- frames by type ---")
    seen = {}
    for f in res["frames"]:
        seen[f.get("type")] = seen.get(f.get("type"), 0) + 1
    print(json.dumps(seen))
    for f in res["frames"]:
        if f.get("type") != "token":
            print("[frame] %s" % json.dumps({k: v for k, v in f.items()
                                             if k != "document"})[:600])
    terminal = [f for f in res["frames"] if f.get("type") in ("complete", "done")]
    print("\n[terminal] frames=%d  error_frames=%d"
          % (len(terminal), len([f for f in res["frames"] if f.get("type") == "error"])))

    time.sleep(1.5)
    after = snap(pid)
    print("[after] revisions %s->%s audit_rows %s->%s refusal_audit_rows %s->%s"
          % (before["revision_count"], after["revision_count"],
             before["audit_rows"], after["audit_rows"],
             before["refusal_audit_rows"], after["refusal_audit_rows"]))
    print("[after] project_row=%s" % json.dumps(after["project"]))
    print("[after] last_compiled_json=%s" % (after["compiled_json"] or "NULL")[:400])
    print("[scratch project] %s" % pid)


if __name__ == "__main__":
    main()
