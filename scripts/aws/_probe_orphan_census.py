"""Every row in the database whose project is gone, by table."""
import sqlite3
c = sqlite3.connect("prompt_matrix/history.sqlite")
c.row_factory = sqlite3.Row
tables = [r[0] for r in c.execute("select name from sqlite_master where type='table' order by name")]
print("%-26s %-14s %8s %s" % ("table", "key", "orphans", "has FK to projects"))
total = 0
for t in tables:
    cols = {r[1] for r in c.execute("PRAGMA table_info(%s)" % t)}
    key = "project_id" if "project_id" in cols else ("workspace_id" if "workspace_id" in cols else None)
    if not key:
        continue
    n = c.execute(f"select count(*) from {t} x where x.{key} is not null"
                  " and x.{k} != 'default' and not exists (select 1 from projects p where p.id = x.{k})"
                  .format(k=key)).fetchone()[0]
    if not n:
        continue
    fk = any(f["table"] == "projects" for f in c.execute("PRAGMA foreign_key_list(%s)" % t))
    total += n
    print("%-26s %-14s %8d %s" % (t, key, n, "yes" if fk else "NO -> migration"))
print("%-26s %-14s %8d" % ("TOTAL", "", total))
print("\nproject rows: %d" % c.execute("select count(*) from projects").fetchone()[0])
print("PRAGMA foreign_key_check violations (FK tables only): %d" % len(c.execute("PRAGMA foreign_key_check").fetchall()))
