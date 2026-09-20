"""Inspect / clear the two compile-cache stores for a project.
usage: _probe_cache_admin.py <project_id> [clear]
Clears pipeline_cache rows with kind='ast' and the OMP 'ast:<project>:<digest>' memories,
so the next compile is a genuine pipeline run rather than a cache replay.
"""
import sqlite3, sys

pid = sys.argv[1]
clear = len(sys.argv) > 2 and sys.argv[2] == "clear"

app = sqlite3.connect("prompt_matrix/history.sqlite")
rows = app.execute(
    "select cache_key, updated_at, length(payload_json) from pipeline_cache"
    " where project_id=? and kind='ast'", (pid,)).fetchall()
print("pipeline_cache ast rows:", len(rows))
for r in rows:
    print("  ", r)

omp = sqlite3.connect("/home/ubuntu/.omp/omp.db")
mem = omp.execute("select rowid, id, tags from memories where tags like ?",
                  (f'%"ast:{pid}:%',)).fetchall()
print("omp ast memories:", len(mem))
for r in mem:
    print("  ", r)

if clear:
    app.execute("delete from pipeline_cache where project_id=? and kind='ast'", (pid,))
    app.commit()
    for r in mem:
        omp.execute("delete from memories where rowid=?", (r[0],))
    omp.commit()
    left = app.execute("select count(*) from pipeline_cache where project_id=? and kind='ast'", (pid,)).fetchone()[0]
    leftm = omp.execute("select count(*) from memories where tags like ?", (f'%"ast:{pid}:%',)).fetchone()[0]
    print("cleared -> pipeline_cache ast rows left:", left, " omp ast memories left:", leftm)
