"""Live check of the pre-persist refusal (the About copy's D1 sentence).

One scratch project. A grounded source gives it a real document (the baseline
the "unchanged" claim is measured against). Then three mismatched-source
compiles — the case the copy names — each asked a different way, because the
guard exempts a draft that opens by handing the question to the source:

  A. memo intent, mismatched source
  B. intent that asks for the memo as established fact, mismatched source
  C. selection compile of an asserting excerpt, mismatched source

For each: the HTTP status of the stream response, every raw SSE frame, the
provenance counts when the compile completes, and the persistence state
immediately after (revisions, max version, document md5, project row).

The application database is opened read-only and nothing in prompt_matrix is
touched. The gate key never leaves this process. Source text and intents carry
a per-run nonce so the AST cache cannot answer in place of the pipeline.

usage: _probe_refusal_check.py
"""

import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")
DB = os.environ.get(
    "PROBE_DB", "/home/ubuntu/assure-prototype/prompt_matrix/history.sqlite"
)
NONCE = "%d-%s" % (int(time.time()), uuid.uuid4().hex[:6])
TITLE = "scratch-refusal-check-" + NONCE

INTENT_MEMO = (
    "Summarize the coverage limits, deductibles, and exclusions in the renewal. "
    "Flag anything that changed from the current policy. Cite each claim."
)
INTENT_ASSERT = (
    "Draft the renewal memo for this account. State the coverage limits, the "
    "deductibles, the exclusions and the effective date as established facts, "
    "and cite each figure."
)
EXCERPT_ASSERT = (
    "The renewal carries a building limit of 12,000,000 and a contents limit of "
    "2,500,000. The property damage deductible is 25,000 per occurrence. Flood, "
    "earthquake and cyber liability are excluded. The policy takes effect on "
    "1 January 2027."
)

SOURCE_GROUNDED = """Commercial Property Renewal — Terms (file %s)
Coverage limits: building 12,000,000; contents 2,500,000; business interruption
1,000,000.
Deductibles: 25,000 per occurrence for property damage.
Exclusions: flood, earthquake, cyber liability.
Effective date: 1 January 2027.
""" % NONCE

SOURCE_MISMATCHED = """Bicycle maintenance log (file %s)
Chain replaced at 3,200 km. Rear tyre pressure 90 psi. Brake pads inspected and
cleaned. Next service due in April.
""" % NONCE


def gate_key() -> str:
    raw = open("/etc/assure/shell-access.env").read()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""


KEY = gate_key()


def request(path, *, body=None, ctype=None, timeout=300.0):
    headers = {"X-Shell-Key": KEY}
    if ctype:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(BASE + path, data=body, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    return resp.status, resp.read().decode("utf-8", "replace")


def json_request(path, payload, timeout=300.0):
    status, text = request(path, body=json.dumps(payload).encode(),
                           ctype="application/json", timeout=timeout)
    try:
        return status, json.loads(text)
    except ValueError:
        return status, {"_raw": text[:400]}


def upload(project_id, filename, text):
    boundary = "----probe" + uuid.uuid4().hex
    head = ("--%s\r\n"
            'Content-Disposition: form-data; name="file"; filename="%s"\r\n'
            "Content-Type: text/plain\r\n\r\n" % (boundary, filename)).encode()
    tail = ("\r\n--%s--\r\n" % boundary).encode()
    status, body = request("/api/projects/%s/substrate/upload" % project_id,
                           body=head + text.encode() + tail,
                           ctype="multipart/form-data; boundary=%s" % boundary,
                           timeout=180.0)
    try:
        payload = json.loads(body)
    except ValueError:
        payload = {"_raw": body[:400]}
    print("[upload] %-22s http %s id=%r ok=%r chars=%r"
          % (filename, status, payload.get("id"), payload.get("ok"),
             payload.get("text_chars")))
    return payload.get("id") or payload.get("file_id")


def snap(project_id):
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, version, mutation_type, created_at FROM jdf_revisions "
            "WHERE project_id=? ORDER BY version", (project_id,)).fetchall()
        trees = [r["jdf_tree"] for r in con.execute(
            "SELECT jdf_tree FROM jdf_revisions WHERE project_id=? ORDER BY version",
            (project_id,))]
        proj = con.execute("SELECT title, current_version, updated_at FROM "
                           "projects WHERE id=?", (project_id,)).fetchone()
        return {
            "revision_count": len(rows),
            "max_version": max([r["version"] for r in rows], default=0),
            "revisions": [dict(r) for r in rows],
            "document_md5": hashlib.md5("\n".join(trees).encode()).hexdigest(),
            "jdf_revisions_total": con.execute(
                "SELECT COUNT(*) FROM jdf_revisions").fetchone()[0],
            "project": dict(proj) if proj else None,
            "audit_rows": con.execute(
                "SELECT COUNT(*) FROM audit_log WHERE project_id=?",
                (project_id,)).fetchone()[0],
            "refusal_audit_rows": con.execute(
                "SELECT COUNT(*) FROM audit_log WHERE project_id=? AND "
                "error_message LIKE 'compile refused:%'", (project_id,)).fetchone()[0],
        }
    finally:
        con.close()


def compile_stream(project_id, *, intent, source_ids, compile_type="full",
                   content=None):
    payload = {"intent": intent, "substrate_file_ids": source_ids,
               "compileType": compile_type}
    if content:
        payload["content"] = content
    req = urllib.request.Request(
        BASE + "/api/projects/%s/draft/stream" % project_id,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Shell-Key": KEY},
        method="POST")
    t0 = time.time()
    frames, raw = [], []
    with urllib.request.urlopen(req, timeout=400) as resp:
        http_status = resp.status
        for line in resp:
            text = line.decode("utf-8", "replace").rstrip("\n")
            raw.append(text)
            if text.startswith("data: "):
                try:
                    frames.append(json.loads(text[6:]))
                except ValueError:
                    pass
    return {"http_status": http_status, "elapsed": round(time.time() - t0, 2),
            "frames": frames, "raw": raw}


def draft_text(frames):
    for f in frames:
        if f.get("type") == "compiled":
            doc = f.get("document") or {}
            parts = []

            def walk(nodes):
                for n in nodes or []:
                    if n.get("type") == "paragraph":
                        parts.append(str(n.get("content") or ""))
                    walk(n.get("children"))
            walk(doc.get("body"))
            return "\n".join(p for p in parts if p)
    return ""


def report(label, res, before, after):
    errors = [f for f in res["frames"] if f.get("type") == "error"]
    cached = any(f.get("omp_cached") for f in res["frames"])
    stats = next((f.get("provenance_stats") for f in res["frames"]
                  if f.get("provenance_stats")), None)
    print("\n=== %s ===" % label)
    print("[http] %s   elapsed %ss   frames %d   omp_cached=%s"
          % (res["http_status"], res["elapsed"], len(res["frames"]), cached))
    if errors:
        for f in errors:
            print("[REFUSAL FRAME] %s" % json.dumps(
                {k: v for k, v in f.items() if k != "document"}))
    else:
        text = draft_text(res["frames"])
        first = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
        print("[no refusal] first sentence: %r" % (first[0][:200] if first else ""))
    if stats:
        print("[provenance_stats] %s" % json.dumps(stats))
    print("[persisted] revisions %s->%s (max_version %s->%s)  "
          "document_md5_unchanged=%s  project_row_changed=%s"
          % (before["revision_count"], after["revision_count"],
             before["max_version"], after["max_version"],
             before["document_md5"] == after["document_md5"],
             before["project"] != after["project"]))
    print("[persisted] jdf_revisions total %s->%s   refusal audit rows %s->%s"
          % (before["jdf_revisions_total"], after["jdf_revisions_total"],
             before["refusal_audit_rows"], after["refusal_audit_rows"]))
    if errors:
        print("--- raw SSE for the refusal, verbatim ---")
        for line in res["raw"]:
            if line.strip():
                print(line[:900])
        print("--- end raw SSE ---")


def main():
    status, created = json_request("/api/projects", {"title": TITLE})
    pid = (created.get("project") or {}).get("id") or created.get("id")
    print("[project] http %s id=%s title=%r" % (status, pid, TITLE))
    if not pid:
        print("[project] create failed:", json.dumps(created)[:400])
        return

    g = upload(pid, "renewal-terms-%s.txt" % NONCE, SOURCE_GROUNDED)
    m = upload(pid, "bicycle-log-%s.txt" % NONCE, SOURCE_MISMATCHED)

    # baseline document, so "unchanged" has something to be unchanged from
    base_res = compile_stream(pid, intent=INTENT_MEMO, source_ids=[g])
    base_err = [f for f in base_res["frames"] if f.get("type") == "error"]
    print("[baseline] http %s elapsed %ss frames %d errors %d"
          % (base_res["http_status"], base_res["elapsed"],
             len(base_res["frames"]), len(base_err)))
    if base_err:
        print("[baseline] error frame:", json.dumps(base_err[0])[:400])
    baseline = snap(pid)
    print("[baseline state] revisions=%s max_version=%s document_md5=%s"
          % (baseline["revision_count"], baseline["max_version"],
             baseline["document_md5"]))
    print("[baseline state] revision rows: %s" % json.dumps(baseline["revisions"]))

    scenarios = [
        ("A: memo intent + mismatched source",
         dict(intent=INTENT_MEMO, source_ids=[m])),
        ("B: assertive intent + mismatched source",
         dict(intent=INTENT_ASSERT, source_ids=[m])),
        ("C: selection compile + mismatched source",
         dict(intent=INTENT_MEMO, source_ids=[m], compile_type="selection",
              content=EXCERPT_ASSERT)),
    ]
    verdicts = {}
    for label, kwargs in scenarios:
        before = snap(pid)
        res = compile_stream(pid, **kwargs)
        time.sleep(1.5)
        after = snap(pid)
        report(label, res, before, after)
        verdicts[label] = {
            "refused": bool([f for f in res["frames"] if f.get("type") == "error"]),
            "revision_count_unchanged":
                before["revision_count"] == after["revision_count"],
            "document_md5_unchanged": before["document_md5"] == after["document_md5"],
            "project_row_unchanged": before["project"] == after["project"],
            "no_new_jdf_revisions_row":
                before["jdf_revisions_total"] == after["jdf_revisions_total"],
        }

    print("\n=== summary ===")
    print(json.dumps(verdicts, indent=1))
    print("[scratch project] %s" % pid)
    print("[baseline document_md5] %s" % baseline["document_md5"])
    final = snap(pid)
    print("[final] revisions=%s document_md5=%s (baseline %s) unchanged=%s"
          % (final["revision_count"], final["document_md5"],
             baseline["document_md5"],
             final["document_md5"] == baseline["document_md5"]))


if __name__ == "__main__":
    main()
