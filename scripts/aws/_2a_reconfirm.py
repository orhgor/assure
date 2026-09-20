"""2A — read-only re-confirmation on the final shipping code, after the last bounce.

GET /health and SELECT-only reads of the live DB. Creates nothing, deletes nothing,
writes nothing, and takes a WAL read like any other reader — so it cannot disturb a
sibling's live window. Establishes that the migration's properties are still in
force on the code the box is now running, not merely at the time it was first
verified.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import urllib.request

APP = "http://127.0.0.1:8890"
DB = "/home/ubuntu/assure-prototype/prompt_matrix/history.sqlite"
MIGRATED = ("audit_log", "jdf_documents", "node_revisions", "pipeline_cache",
            "project_budgets", "token_ledger_entries", "user_activity_log")
SENTINEL = "default"


def read_db():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def main() -> int:
    with urllib.request.urlopen(f"{APP}/health", timeout=30) as r:
        h = json.loads(r.read())
    print(f"health http {r.status} status={h.get('status')} ok={h.get('ok')} "
          f"sqlite={h.get('checks', {}).get('sqlite')} build_sha={h.get('build_sha')}")

    con = read_db()
    refs = []
    for (t,) in con.execute("select name from sqlite_master where type='table' "
                            "and name not like 'sqlite_%' order by name"):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
        fks = [f for f in con.execute(f"PRAGMA foreign_key_list({t})") if f["table"] == "projects"]
        if fks:
            refs.append((t, fks[0]["from"], (fks[0]["on_delete"] or "NO ACTION").upper()))
        elif "project_id" in cols:
            refs.append((t, "project_id", "NO FK"))

    bad = [(t, c, a) for t, c, a in refs if t in MIGRATED and a != "CASCADE"]
    print(f"project-referencing tables: {len(refs)}   migrated tables NOT cascading: {bad or 'none'}")

    orphans = {}
    for t, col, _a in refs:
        n = con.execute(
            f"select count(*) from {t} where {col} is not null and {col} != ? "
            f"and not exists (select 1 from projects p where p.id = {col})", (SENTINEL,)).fetchone()[0]
        if n:
            orphans[t] = n
    fk = len(con.execute("PRAGMA foreign_key_check").fetchall())
    print(f"orphans across all {len(refs)} tables: {orphans or 'NONE'}   foreign_key_check: {fk}")

    p = con.execute("select current_version, updated_at, length(last_compiled_json) lc "
                    "from projects where id='demo-3235f5'").fetchone()
    print(f"demo-3235f5: current_version={p['current_version']} updated_at={p['updated_at']} "
          f"last_compiled_json={p['lc']}B")
    print(f"projects: {con.execute('select count(*) from projects').fetchone()[0]}")
    con.close()
    ok = not orphans and fk == 0 and not bad and h.get("ok") is True
    print("2A RECONFIRM:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
