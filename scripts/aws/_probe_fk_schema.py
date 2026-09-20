import re, sqlite3
c = sqlite3.connect("prompt_matrix/history.sqlite")
TABLES = ["jdf_revisions","substrate_vault","substrates","jdf_documents","node_revisions","audit_log",
          "source_conflicts","project_budgets","token_ledger_entries","project_comments",
          "daily_compile_limits","workspace_settings","document_locks","sign_offs","runs",
          "drafts","pipeline_cache","projects"]
for t in TABLES:
    row = c.execute("select sql from sqlite_master where name=? and type='table'", (t,)).fetchone()
    if not row:
        print("== %-22s MISSING" % t); continue
    sql = row[0] or ""
    fks = re.findall(r"FOREIGN KEY[^,\n]*(?:\([^)]*\))?[^,\n]*", sql)
    casc = "ON DELETE CASCADE" in sql.upper()
    print("== %-22s on_delete_cascade=%s" % (t, casc))
    if fks:
        for f in fks:
            print("      %s" % " ".join(f.split()))
    else:
        print("      (no FOREIGN KEY clause)")
