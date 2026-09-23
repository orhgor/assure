import sqlite3
c = sqlite3.connect("prompt_matrix/history.sqlite")
for t in ("jdf_documents", "node_revisions"):
    print("=" * 72)
    print(c.execute("select sql from sqlite_master where name=? and type='table'", (t,)).fetchone()[0].strip())
    for name, sql in c.execute("select name, sql from sqlite_master where tbl_name=? and type='index' and sql is not null", (t,)):
        print("   INDEX %s: %s" % (name, " ".join((sql or "").split())))
