"""2A — LIVE verification after the FK/retention migration.

Reads the served schema, then runs the whole golden cycle against the running app
on 8891: create a scratch project, upload (ingest) a real PDF, compile it, delete
the project — and counts orphans in EVERY table that carries a project reference,
before and after. A copy-and-swap that lost the FK, or an app connection that
never enables `PRAGMA foreign_keys`, shows up here as rows left behind.

usage: _2a_verify.py [intent-file]
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

DB = "/home/ubuntu/assure-prototype/prompt_matrix/history.sqlite"
STATIC = "http://127.0.0.1:8891"
APP = "http://127.0.0.1:8890"
PDF = Path(os.environ.get("CASCADE_PDF", "/home/ubuntu/real-docs/cp10300917-sample.pdf"))
INTENT_FILE = "/home/ubuntu/probes/_demo_intent_v2.txt"
DEMO = os.environ.get("DEMO_PROJECT", "demo-3235f5")
DEMO_INTENT = os.environ.get("DEMO_INTENT", "/home/ubuntu/probes/_demo_intent_v2.txt")
INTENT_DEFAULT = "Summarize the obligations in the attached source."
MIGRATED = ("audit_log", "jdf_documents", "node_revisions", "pipeline_cache",
            "project_budgets", "token_ledger_entries", "user_activity_log")
SENTINEL = "default"


def key() -> str:
    raw = Path("/etc/assure/shell-access.env").read_text()
    m = re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw)
    return m.group(1).strip() if m else ""


HEAD = {"X-Shell-Key": key(), "User-Agent": "curl/8.7.1"}


def req(method: str, path: str, body=None, data=None, ctype=None, base=STATIC, timeout=300):
    h = dict(HEAD)
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if data is not None:
        payload = data
        h["Content-Type"] = ctype
    r = urllib.request.Request(base + path, data=payload, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return resp.status, {"raw": raw[:200].decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read()[:400].decode("utf-8", "replace")}
    except Exception as exc:
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def read_db():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def referenced_tables(con) -> list[tuple[str, str]]:
    """Every table carrying a project reference, and the column that carries it."""
    out = []
    for (t,) in con.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
        fks = [f for f in con.execute(f"PRAGMA foreign_key_list({t})") if f["table"] == "projects"]
        if fks:
            out.append((t, fks[0]["from"]))
        elif "project_id" in cols:
            out.append((t, "project_id"))
        elif "workspace_id" in cols:
            out.append((t, "workspace_id"))
    return out


def orphan_census(con) -> tuple[dict, int]:
    out = {}
    for t, col in referenced_tables(con):
        out[t] = con.execute(
            f"select count(*) from {t} where {col} is not null and {col} != ? "
            f"and not exists (select 1 from projects p where p.id = {col})", (SENTINEL,)).fetchone()[0]
    return out, len(con.execute("PRAGMA foreign_key_check").fetchall())


def live_schema(con) -> dict:
    out = {}
    for t, _col in referenced_tables(con):
        fks = [f for f in con.execute(f"PRAGMA foreign_key_list({t})") if f["table"] == "projects"]
        out[t] = (fks[0]["from"], (fks[0]["on_delete"] or "NO ACTION").upper()) if fks else (None, "NO FK")
    return out


def multipart(field: str, filename: str, blob: bytes):
    b = "----cascade" + uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            "Content-Type: application/pdf\r\n\r\n").encode() + blob + f"\r\n--{b}--\r\n".encode()
    return body, f"multipart/form-data; boundary={b}"


def stream_compile(pid: str, intent: str, sub_ids: list[str] | None = None,
                   timeout: float = 420.0) -> tuple[int, dict]:
    """POST the draft stream and collect the SSE frames (the compile path)."""
    h = dict(HEAD)
    h["Content-Type"] = "application/json"
    payload = json.dumps({"intent": intent, "substrate_file_ids": sub_ids or []}).encode()
    r = urllib.request.Request(f"{STATIC}/api/projects/{pid}/draft/stream", data=payload, headers=h, method="POST")
    events, frames, err = [], 0, None
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            for line in resp:
                text = line.decode("utf-8", "replace").rstrip("\n")
                if text.startswith("event: "):
                    events.append(text[7:].strip())
                elif text.startswith("data: "):
                    frames += 1
                    if '"type": "error"' in text or '"type":"error"' in text:
                        err = text[:300]
            return resp.status, {"events": events, "frames": frames, "error": err}
    except urllib.error.HTTPError as e:
        return e.code, {"events": events, "frames": frames, "error": e.read()[:300].decode("utf-8", "replace")}
    except Exception as exc:
        return 0, {"events": events, "frames": frames, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    print("=== 1. health ===")
    for name, base in (("app 8890", APP), ("gate 8891", STATIC)):
        st, body = req("GET", "/health", base=base, timeout=30)
        print(f"  {name}: http {st} {json.dumps(body)[:200]}")

    con = read_db()
    print("\n=== 2. served schema (project references) ===")
    sch = live_schema(con)
    for t, (col, act) in sch.items():
        flag = ""
        if t in MIGRATED:
            flag = "  <-- migrated" + (" OK" if act == "CASCADE" else " ** NOT CASCADE **")
        print(f"  {t:<24} {str(col):<14} {act}{flag}")
    missing = [t for t in MIGRATED if sch.get(t, (None, ""))[1] != "CASCADE"]
    print(f"  migrated tables not cascading: {missing or 'none'}")

    print("\n=== 3. pre-cycle orphan census (whole live DB) ===")
    before, fk_before = orphan_census(con)
    nz = {k: v for k, v in before.items() if v}
    print(f"  tables with a project reference: {len(before)}")
    print(f"  orphans now: {nz or 'NONE'}   foreign_key_check violations: {fk_before}")

    print("\n=== 4. golden cycle: create -> upload -> compile -> delete ===")
    title = f"retention-2a-{int(time.time())}"
    st, created = req("POST", "/api/projects", body={"title": title})
    pid = (created or {}).get("id")
    print(f"  create http={st} project={pid}")
    if not pid:
        print(f"  FAIL — could not create a scratch project: {json.dumps(created)[:300]}")
        return 1

    def rows(con, pid):
        out = {}
        for t, col in referenced_tables(con):
            try:
                out[t] = con.execute(f"select count(*) from {t} where {col}=?", (pid,)).fetchone()[0]
            except sqlite3.Error:
                pass
        return out

    c0 = rows(con, pid)
    blob, ctype = multipart("file", PDF.name, PDF.read_bytes())
    st, ing = req("POST", f"/api/projects/{pid}/jdf/ingest", data=blob, ctype=ctype)
    c1 = rows(con, pid)
    print(f"  upload http={st} ok={ing.get('ok')} chunks={ing.get('chunks_stored')}")
    print(f"    rows written: { {k: (c0.get(k, 0), c1.get(k, 0)) for k in c1 if c0.get(k, 0) != c1.get(k, 0)} or 'NONE'}")

    sub_ids = [r[0] for r in con.execute("select id from substrate_vault where project_id=?", (pid,))]
    print(f"  sources attached (substrate_file_ids): {sub_ids}")
    intent = Path(INTENT_FILE).read_text().strip() if Path(INTENT_FILE).exists() else "Draft the document"
    st, comp = stream_compile(pid, intent, sub_ids)
    c2 = rows(con, pid)
    print(f"  compile http={st} events={comp['events']} error={str(comp['error'])[:200]}")
    print(f"    rows written: { {k: (c1.get(k, 0), c2.get(k, 0)) for k in c2 if c1.get(k, 0) != c2.get(k, 0)} or 'NONE'}")

    st, dele = req("DELETE", f"/api/projects/{pid}")
    time.sleep(1.0)
    con.close()
    con = read_db()
    c3 = rows(con, pid)
    print(f"  delete http={st} {json.dumps(dele)[:120]}")
    print(f"    rows surviving the delete: { {k: v for k, v in c3.items() if v} or 'NONE'}")

    after, fk_after = orphan_census(con)
    nz_after = {k: v for k, v in after.items() if v}
    print("\n=== 5. post-cycle orphan census (whole live DB) ===")
    print(f"  orphans: {nz_after or 'NONE'}   foreign_key_check violations: {fk_after}")
    con.close()

    print("\n=== 6. the demo document compiles (demo-3235f5) ===")
    con = read_db()
    dsub = [r[0] for r in con.execute(
        "select id from substrate_vault where project_id=? and included=1", (DEMO,))]
    d_before = {t: con.execute(f"select count(*) from {t} where {col}=?", (DEMO,)).fetchone()[0]
                for t, col in referenced_tables(con)}
    d_intent = Path(DEMO_INTENT).read_text().strip() if Path(DEMO_INTENT).exists() else INTENT_DEFAULT
    dst, dcomp = stream_compile(DEMO, d_intent, dsub)
    time.sleep(1.0)
    con.close()
    con = read_db()
    d_after = {t: con.execute(f"select count(*) from {t} where {col}=?", (DEMO,)).fetchone()[0]
               for t, col in referenced_tables(con)}
    print(f"  sources={dsub} http={dst} events={dcomp['events']} error={str(dcomp['error'])[:200]}")
    print(f"  rows written: { {k: (d_before.get(k, 0), d_after.get(k, 0)) for k in d_after if d_before.get(k, 0) != d_after.get(k, 0)} or 'NONE'}")
    demo_ok = dcomp.get("error") is None and "complete" in dcomp["events"] and dcomp["frames"] > 0
    print(f"  demo compile: {'OK' if demo_ok else 'FAILED'}")
    con.close()

    ok = (not nz_after and fk_after == 0 and not missing
          and not {k: v for k, v in c3.items() if v} and c1 != c0 and demo_ok)
    print("\nVERDICT:", "PASS — zero orphans across every project-referencing table, and the cycle landed rows"
          if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
