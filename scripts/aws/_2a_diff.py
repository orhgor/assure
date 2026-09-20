"""2A — the swap's structural diff: pre-migration backup vs live DB.

For each rebuilt table, compares the column list and the index DDL set. A
copy-and-swap that silently dropped a column or an index would be invisible to
the orphan counts, and the index review depends on knowing exactly what changed.
"""
from __future__ import annotations

import sqlite3
import sys

LIVE = "/home/ubuntu/assure-prototype/prompt_matrix/history.sqlite"
BAK = "/home/ubuntu/backups/history.sqlite.20260918T194353Z.bak"
REBUILT = ("audit_log", "jdf_documents", "node_revisions", "pipeline_cache",
           "project_budgets", "token_ledger_entries", "user_activity_log")


def snap(path: str) -> dict:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = {}
    for t in REBUILT:
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
        idx = sorted(r[0] for r in c.execute(
            "select sql from sqlite_master where tbl_name=? and type='index' and sql is not null", (t,)))
        ddl = c.execute("select sql from sqlite_master where name=? and type='table'", (t,)).fetchone()
        out[t] = {"cols": cols, "idx": idx, "ddl": " ".join((ddl[0] if ddl else "").split())}
    c.close()
    return out


def main() -> int:
    b, l = snap(BAK), snap(LIVE)
    bad = []
    for t in REBUILT:
        same_cols = b[t]["cols"] == l[t]["cols"]
        same_idx = b[t]["idx"] == l[t]["idx"]
        print(f"{t}")
        print(f"  cols equal: {same_cols} ({len(b[t]['cols'])} cols)")
        print(f"  indexes equal: {same_idx} ({len(b[t]['idx'])} declared)")
        if not same_cols:
            bad.append(t)
            print(f"    before: {b[t]['cols']}")
            print(f"    after : {l[t]['cols']}")
        if not same_idx:
            bad.append(t)
            print(f"    before: {b[t]['idx']}")
            print(f"    after : {l[t]['idx']}")
        print(f"  live ddl: {l[t]['ddl'][:200]}")
    print("\nSTRUCTURAL DIFF:", "IDENTICAL apart from the added FK" if not bad else f"DIFFERENCES in {bad}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
