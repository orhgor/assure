"""Pre-flight for PRAGMA foreign_keys=ON.

1. Existing FK violations per FK table. A row whose parent project is gone proves
   the app has been writing rows with missing parents; enabling the pragma would
   turn that write into a failure.
2. PRAGMA foreign_key_check over the whole database.
"""
import sqlite3
c = sqlite3.connect("prompt_matrix/history.sqlite")
c.row_factory = sqlite3.Row
FK_TABLES = {
    "daily_compile_limits": "project_id", "drafts": "workspace_id",
    "jdf_revisions": "project_id", "project_comments": "project_id",
    "runs": "workspace_id", "substrate_vault": "project_id",
    "substrates": "project_id", "workspace_settings": "project_id",
    "redhat_findings": "run_id",
}
print("== rows whose FK parent is missing ==")
total = 0
for t, col in FK_TABLES.items():
    try:
        n = c.execute(
            f"select count(*) from {t} x where x.{col} is not null"
            f" and not exists (select 1 from projects p where p.id = x.{col})").fetchone()[0]
    except sqlite3.OperationalError as exc:
        print("   %-24s ERR %s" % (t, exc)); continue
    total += n
    print("   %-24s %-14s violating rows=%d" % (t, col, n))
print("   TOTAL:", total)
print("\n== PRAGMA foreign_key_check (whole db) ==")
rows = c.execute("PRAGMA foreign_key_check").fetchall()
print("   violations:", len(rows))
for r in rows[:10]:
    print("   ", tuple(r))
