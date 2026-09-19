import sqlite3
c = sqlite3.connect("prompt_matrix/history.sqlite")
c.row_factory = sqlite3.Row
print("== projects ==")
for r in c.execute("select id,title,created_at from projects order by created_at"):
    d=dict(r); print(f"{d['id']:<28} | {str(d['title'])[:20]:<20} | {d['created_at']}")
print("== substrate ==")
for r in c.execute("""select s.project_id,s.id,s.filename,s.included,length(s.extracted_text) tlen,s.page_count
                      from substrate_vault s order by s.created_at"""):
    d=dict(r); print(f"{d['project_id']:<28} | {d['id']:<24} | {str(d['filename'])[:38]:<38} | incl={d['included']} | tlen={d['tlen']} | pages={d['page_count']}")
