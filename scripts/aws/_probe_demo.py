import sqlite3
c = sqlite3.connect("prompt_matrix/history.sqlite")
c.row_factory = sqlite3.Row
print("== all substrate rows ==")
for r in c.execute("""select s.project_id, s.filename, s.file_size_bytes, s.included, s.created_at,
                             length(s.extracted_text) tlen, p.title
                      from substrate_vault s left join projects p on p.id = s.project_id
                      order by s.created_at"""):
    d = dict(r)
    print(f"{d['created_at']} | {d['project_id']:<26} | {str(d['title']):<14} | {d['filename']:<45} | {d['file_size_bytes']:>8} | incl={d['included']} | tlen={d['tlen']}")
