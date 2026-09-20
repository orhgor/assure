import sqlite3
c = sqlite3.connect("prompt_matrix/history.sqlite")
row = c.execute("select extracted_text from substrate_vault where id='sub-d3eab1f0fa9c486d'").fetchone()
t = row[0]
print("LEN", len(t))
print("=" * 70)
print(t)
