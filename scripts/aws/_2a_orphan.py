"""2A — the orphan that appeared after the last bounce: which row, which project, which path.

Read-only. Prints the offending audit_log row, the project it names, whether that
project still exists, when the row was written relative to sibling writes, and what
else in the DB still names the same project id — which is what distinguishes "the
delete cascade failed" from "a write landed after the delete".
"""
from __future__ import annotations

import sqlite3
import sys

DB = "/home/ubuntu/assure-prototype/prompt_matrix/history.sqlite"
SENTINEL = "default"


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    rows = con.execute(
        "select id, request_id, project_id, action, success, duration_ms, error_message, created_at "
        "from audit_log where project_id is not null and project_id != ? "
        "and not exists (select 1 from projects p where p.id = audit_log.project_id) "
        "order by created_at", (SENTINEL,)).fetchall()
    print(f"orphan rows in audit_log: {len(rows)}")
    for r in rows:
        print(" ", dict(r))

    for r in rows:
        pid = r["project_id"]
        print(f"\n=== the project it names: {pid!r} ===")
        print("  projects row:", dict(con.execute(
            "select id,title,created_at,updated_at,current_version from projects where id=?",
            (pid,)).fetchone() or {}) or "GONE")
        print("  other rows still naming it:")
        for t, col in (("jdf_documents", "project_id"), ("jdf_revisions", "project_id"),
                       ("node_revisions", "project_id"), ("substrate_vault", "project_id"),
                       ("pipeline_cache", "project_id"), ("project_budgets", "project_id"),
                       ("token_ledger_entries", "project_id"), ("user_activity_log", "project_id"),
                       ("daily_compile_limits", "project_id"), ("drafts", "workspace_id")):
            n = con.execute(f"select count(*) from {t} where {col}=?", (pid,)).fetchone()[0]
            if n:
                print(f"    {t}: {n}")
        print("  audit rows for it, oldest first:")
        for a in con.execute(
                "select action, success, created_at from audit_log where project_id=? "
                "order by created_at", (pid,)):
            print("    ", tuple(a))

    print("\n=== all audit actions seen since 20:00, newest last ===")
    for a in con.execute("select action, count(*) n, min(created_at) f, max(created_at) l "
                         "from audit_log where created_at >= '2026-09-18 20:00' "
                         "group by action order by f"):
        print("   ", tuple(a))

    print("\n=== projects created/deleted since 20:00 ===")
    for p in con.execute("select id, title, created_at from projects "
                         "where created_at >= '2026-09-18 20:00' order by created_at"):
        print("   ", tuple(p))
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
