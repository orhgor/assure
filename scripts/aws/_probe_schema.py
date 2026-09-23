import sqlite3
c = sqlite3.connect("prompt_matrix/history.sqlite")
for t in ("pipeline_cache","redhat_cache","substrates","project_templates","schema_migrations"):
    print("==", t, "==")
    try:
        print(c.execute(f"select sql from sqlite_master where name=?", (t,)).fetchone()[0])
    except Exception as e:
        print("err", e)
print("== rows ==")
for t in ("pipeline_cache","redhat_cache","schema_migrations"):
    try:
        print(t, c.execute(f"select count(*) from {t}").fetchone()[0])
    except Exception as e:
        print(t, "err", e)
print("== migrations (last 12) ==")
for r in c.execute("select * from schema_migrations order by rowid desc limit 12"):
    print(r)
