"""Every table that references projects, its ON DELETE action, and its live row count."""
import sqlite3
c = sqlite3.connect("prompt_matrix/history.sqlite")
c.row_factory = sqlite3.Row
tables = [r[0] for r in c.execute("select name from sqlite_master where type='table' order by name")]
print("%-26s %-14s %-18s %s" % ("table", "fk column", "on delete", "rows for any project?"))
casc, setnull, noaction, nofk = [], [], [], []
for t in tables:
    fks = c.execute("PRAGMA foreign_key_list(%s)" % t).fetchall()
    to_proj = [f for f in fks if f["table"] == "projects"]
    if not to_proj:
        n = c.execute("select count(*) from %s" % t).fetchone()[0]
        nofk.append((t, n)); continue
    for f in to_proj:
        act = (f["on_delete"] or "NO ACTION").upper()
        print("%-26s %-14s %-18s %s" % (t, f["from"], act, "yes" if c.execute("select count(*) from %s" % t).fetchone()[0] else "0 rows"))
        {"CASCADE": casc, "SET NULL": setnull}.get(act, noaction).append(t)
print("\nCASCADE   (%d): %s" % (len(casc), casc))
print("SET NULL  (%d): %s" % (len(setnull), setnull))
print("NO ACTION (%d): %s" % (len(noaction), noaction))
print("\ntables with a project_id/workspace_id column but NO FK to projects (%d):" % len([x for x in nofk if x[1]]))
for t, n in nofk:
    cols = {r[1] for r in c.execute("PRAGMA table_info(%s)" % t)}
    if ("project_id" in cols or "workspace_id" in cols) and n:
        print("   %-26s rows=%-6d holds %s" % (t, n, sorted(cols & {"project_id", "workspace_id"})))
