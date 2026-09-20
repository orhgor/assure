import sqlite3, sys
c = sqlite3.connect("prompt_matrix/history.sqlite")
print(c.execute("select sql from sqlite_master where name='token_ledger_entries'").fetchone()[0])
limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10
for r in c.execute("select * from token_ledger_entries order by rowid desc limit ?", (limit,)):
    print(str(r)[:300])
