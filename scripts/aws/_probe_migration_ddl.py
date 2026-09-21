"""Exact DDL for the six tables a project delete still orphans (no FK clause)."""
import sqlite3
c = sqlite3.connect("prompt_matrix/history.sqlite")
for t in ("jdf_documents", "node_revisions", "audit_log", "project_budgets",
          "token_ledger_entries", "pipeline_cache"):
    row = c.execute("select sql from sqlite_master where name=? and type='table'", (t,)).fetchone()
    idx = c.execute("select name, sql from sqlite_master where tbl_name=? and type='index' and sql is not null", (t,)).fetchall()
    print("=" * 72)
    print(row[0].strip() if row else "(missing)")
    for name, sql in idx:
        print("   INDEX %s: %s" % (name, " ".join((sql or "").split())))
