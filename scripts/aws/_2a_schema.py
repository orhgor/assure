"""2A — dump the LIVE schema: every table, its FK to projects, ON DELETE action, row counts.

Read-only (mode=ro). Ground truth for step 1 and 2 of the 2A ticket.
"""
from __future__ import annotations

import sqlite3
import sys

DB = "/home/ubuntu/assure-prototype/prompt_matrix/history.sqlite"


def main() -> int:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    tables = [r[0] for r in c.execute(
        "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
    idx = [r[0] for r in c.execute(
        "select name from sqlite_master where type='index' and name not like 'sqlite_%' order by name")]
    print(f"DB={DB}")
    print(f"tables ({len(tables)}): {tables}")
    print(f"indexes ({len(idx)}): {len(idx)}")
    print()

    cascade, other, nofk = [], [], []
    for t in tables:
        n = c.execute(f"select count(*) from {t}").fetchone()[0]
        fks = c.execute(f"PRAGMA foreign_key_list({t})").fetchall()
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
        to_proj = [f for f in fks if f["table"] == "projects"]
        pk_cols = [k for k in ("project_id", "workspace_id") if k in cols]
        note = ""
        if not to_proj:
            note = "NO FK TO projects" + (f" (has {pk_cols})" if pk_cols else "")
            nofk.append((t, n, pk_cols))
        else:
            for f in to_proj:
                act = (f["on_delete"] or "NO ACTION").upper()
                if act == "CASCADE":
                    cascade.append((t, f["from"]))
                else:
                    other.append((t, f["from"], act))
                note += f"->projects({f['from']}) ON DELETE {act}; "
        print(f"  {t:<26} rows={n:<7} {note}")

    print()
    print(f"CASCADE to projects ({len(cascade)}): {cascade}")
    print(f"NOT cascade ({len(other)}): {other}")
    print()
    print("== tables with a project_id/workspace_id column but NO FK to projects ==")
    for t, n, cols in nofk:
        if cols:
            print(f"  {t:<26} rows={n:<7} cols={cols}")
    print()
    print("== all tables, full FK list ==")
    for t in tables:
        for f in c.execute(f"PRAGMA foreign_key_list({t})"):
            print(f"  {t}.{f['from']} -> {f['table']}.{f['to']} ON DELETE {(f['on_delete'] or 'NO ACTION').upper()} ON UPDATE {(f['on_update'] or 'NO ACTION').upper()}")
    print()
    print("== PRAGMA foreign_key_check ==")
    viol = c.execute("PRAGMA foreign_key_check").fetchall()
    print(f"violations: {len(viol)}")
    for v in viol[:20]:
        print("   ", tuple(v))
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
