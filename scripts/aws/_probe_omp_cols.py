import sqlite3
omp = sqlite3.connect("/home/ubuntu/.omp/omp.db")
print([d[1] for d in omp.execute("pragma table_info(memories)")])
print("ast memories for demo project:")
cols = [d[1] for d in omp.execute("pragma table_info(memories)")]
tot = 0
for r in omp.execute("select rowid, id, tags from memories where tags like ?", ("%demo-3235f5%",)):
    print("  ", r)
    tot += 1
print("total", tot)
