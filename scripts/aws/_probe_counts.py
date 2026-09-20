import sqlite3, sys
c = sqlite3.connect("prompt_matrix/history.sqlite")
pid = sys.argv[1] if len(sys.argv) > 1 else "demo-3235f5"
TABLES = ["jdf_revisions","substrate_vault","substrates","jdf_documents","node_revisions","audit_log",
          "source_conflicts","project_budgets","token_ledger_entries","project_comments",
          "daily_compile_limits","workspace_settings","document_locks","sign_offs","feedback","runs",
          "drafts","executions","redhat_findings","pipeline_cache","jdf_cli_documents","projects"]
print("project:", pid)
total = 0
for t in TABLES:
    try:
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({t})")}
    except sqlite3.OperationalError as e:
        print(f"  {t:<22} MISSING"); continue
    key = "project_id" if "project_id" in cols else ("workspace_id" if "workspace_id" in cols else None)
    if not key:
        print(f"  {t:<22} no project col"); continue
    n = c.execute(f"SELECT count(*) FROM {t} WHERE {key}=?", (pid,)).fetchone()[0]
    if n: total += n
    print(f"  {t:<22} {n}")
print("  TOTAL rows carrying project_id:", total)
