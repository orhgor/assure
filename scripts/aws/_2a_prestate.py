"""2A — the PRE-migration state, read from the backup taken before the migration.

Read-only. Establishes (a) which tables lacked ON DELETE CASCADE, and (b) how many
orphan rows each held, so the migration's effect can be checked against the
original condition rather than the report it wrote about itself.
"""
from __future__ import annotations

import os
import sqlite3
import sys

BAK = (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else
       "/home/ubuntu/backups/history.sqlite.20260918T194353Z.bak")
SENTINEL = "default"


def main() -> int:
    print(f"backup={BAK} mtime={os.path.getmtime(BAK)} size={os.path.getsize(BAK)}")
    c = sqlite3.connect(f"file:{BAK}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    print("integrity:", c.execute("PRAGMA integrity_check").fetchone()[0])
    tables = [r[0] for r in c.execute(
        "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
    print(f"tables ({len(tables)}): {len(tables)}")
    print(f"projects rows: {c.execute('select count(*) from projects').fetchone()[0]}")
    print()
    print("%-24s %-14s %-10s %-8s %s" % ("table", "fk col", "on delete", "rows", "orphans"))
    lacked, had = [], []
    for t in tables:
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
        col = "workspace_id" if "workspace_id" in cols else ("project_id" if "project_id" in cols else None)
        fks = [f for f in c.execute(f"PRAGMA foreign_key_list({t})") if f["table"] == "projects"]
        n = c.execute(f"select count(*) from {t}").fetchone()[0]
        if not col:
            continue
        if not fks:
            act = "NO FK"
        else:
            act = (fks[0]["on_delete"] or "NO ACTION").upper()
        colname = fks[0]["from"] if fks else col
        try:
            orph = c.execute(
                f"select count(*) from {t} where {colname} is not null and {colname} != ? "
                f"and not exists (select 1 from projects p where p.id = {colname})", (SENTINEL,)
            ).fetchone()[0]
        except sqlite3.Error as exc:
            orph = f"ERR {exc}"
        print("%-24s %-14s %-10s %-8s %s" % (t, colname, act, n, orph))
        (had if act == "CASCADE" else lacked).append(t)
    print()
    print(f"had ON DELETE CASCADE ({len(had)}): {had}")
    print(f"LACKED cascade ({len(lacked)}): {lacked}")
    print()
    print("== PRAGMA foreign_key_list for tables with a project reference ==")
    for t in tables:
        for f in c.execute(f"PRAGMA foreign_key_list({t})"):
            print(f"  {t}.{f['from']} -> {f['table']}.{f['to']} ON DELETE {(f['on_delete'] or 'NO ACTION').upper()}")
    print()
    print("views/triggers:", [r[0] for r in c.execute(
        "select name from sqlite_master where type in ('view','trigger')")])
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
