import sqlite3
c = sqlite3.connect("prompt_matrix/history.sqlite")
t = c.execute("select extracted_text from substrate_vault where id='sub-d3eab1f0fa9c486d'").fetchone()[0]
for i, ln in enumerate(t.split("\n"), 1):
    print(f"{i:3d}| {ln}")
