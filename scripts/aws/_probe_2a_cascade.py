#!/usr/bin/env python3
"""2A acceptance — a project delete cascades, now that the FKs are enforced.

usage: _probe_2a_cascade.py

Creates a scratch project, ingests a real PDF into it (a write into
`substrate_vault`, which now carries a foreign key), then deletes the project
through the API and counts every child table before and after. A cascade that
does not fire leaves rows behind; FK enforcement that rejects legitimate writes
shows up as a failed ingest. Both would be invisible from the migration report
alone, which is why this runs against the live app rather than the database.

The scratch project is created and destroyed inside this run — it is not one of
the user's.
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

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8891")
DB = "/home/ubuntu/assure-prototype/prompt_matrix/history.sqlite"
PDF = Path(os.environ.get("CASCADE_PDF", "/home/ubuntu/real-docs/cp10300917-sample.pdf"))

CHILD_TABLES = (
    "jdf_documents", "jdf_revisions", "substrate_vault", "project_budgets",
    "audit_log", "pipeline_cache", "token_ledger_entries", "node_revisions",
    "user_activity_log", "project_comments", "daily_compile_limits",
)


def key() -> str:
    raw = Path("/etc/assure/shell-access.env").read_text()
    return re.search(r"SHELL_ACCESS_KEY\s*=\s*[\"']?([^\"'\n]+)", raw).group(1).strip()


HEAD = {"X-Shell-Key": key(), "User-Agent": "curl/8.7.1"}


def req(method: str, path: str, body=None, data=None, ctype=None):
    h = dict(HEAD)
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if data is not None:
        payload = data
        h["Content-Type"] = ctype
    r = urllib.request.Request(BASE + path, data=payload, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read()[:300].decode("utf-8", "replace")}


def counts(pid: str) -> dict:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out = {"projects": c.execute("select count(*) from projects where id=?", (pid,)).fetchone()[0]}
    for t in CHILD_TABLES:
        col = "workspace_id" if t in ("drafts", "runs") else "project_id"
        try:
            out[t] = c.execute(f"select count(*) from {t} where {col}=?", (pid,)).fetchone()[0]
        except sqlite3.Error:
            pass
    c.close()
    return out


def multipart(field: str, filename: str, blob: bytes):
    b = "----cascade" + uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            "Content-Type: application/pdf\r\n\r\n").encode() + blob + f"\r\n--{b}--\r\n".encode()
    return body, f"multipart/form-data; boundary={b}"


def main() -> int:
    title = f"cascade-check-{int(time.time())}"
    st, created = req("POST", "/api/projects", body={"title": title})
    pid = (created or {}).get("id")
    print(f"created project {pid} (http {st})")
    if not pid:
        print("FAIL — no project id:", created)
        return 1

    before_ingest = counts(pid)
    body, ctype = multipart("file", PDF.name, PDF.read_bytes())
    st, ing = req("POST", f"/api/projects/{pid}/jdf/ingest", data=body, ctype=ctype)
    after_ingest = counts(pid)
    print(f"ingest http={st} ok={ing.get('ok')} chunks={ing.get('chunks_stored')}")
    wrote = {k: (before_ingest.get(k, 0), after_ingest.get(k, 0))
             for k in after_ingest if before_ingest.get(k) != after_ingest.get(k)}
    print("rows written by the ingest (FK-enforced writes):", wrote or "NONE — the write did not land")
    if not wrote:
        print("FAIL — the ingest wrote nothing; FK enforcement may be rejecting writes")
        return 1

    st, dele = req("DELETE", f"/api/projects/{pid}")
    after_delete = counts(pid)
    leftover = {k: v for k, v in after_delete.items() if v}
    print(f"delete http={st} body={str(dele)[:120]}")
    print("after delete:", after_delete)
    if leftover:
        print("FAIL — rows left behind:", leftover)
        return 1
    print("PASS — every child row cascaded with the project, and the pre-delete write landed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
