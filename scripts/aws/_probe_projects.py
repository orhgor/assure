import sqlite3
c = sqlite3.connect("prompt_matrix/history.sqlite")
c.row_factory = sqlite3.Row
print("== projects ==")
for r in c.execute("select id,title,created_at,updated_at from projects order by created_at"):
    print(dict(r))
print("== substrate_vault cols ==")
print([d[1] for d in c.execute("pragma table_info(substrate_vault)")])
print("== counts ==")
for t in ("projects", "substrate_vault", "drafts", "jdf_documents", "jdf_revisions"):
    print(t, c.execute(f"select count(*) from {t}").fetchone()[0])
