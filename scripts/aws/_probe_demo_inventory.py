"""A1 — inventory every project titled `demo`.

For each: id, title, substrate row count, per-source `included` flag, count of
compile revisions (node_revisions, mutation_type='compile'), and the last
compile timestamp. Read-only.
"""
import json
import sqlite3

DB = "prompt_matrix/history.sqlite"
c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

demos = list(c.execute("SELECT id, title, created_at, updated_at, current_version FROM projects WHERE title = 'demo' ORDER BY created_at"))
print("=== projects titled 'demo': %d ===" % len(demos))
for p in demos:
    pid = p["id"]
    subs = list(c.execute(
        "SELECT id, filename, file_size_bytes, included, length(extracted_text) AS tlen, created_at FROM substrate_vault WHERE project_id = ? ORDER BY created_at",
        (pid,)))
    nsub = len(subs)
    ninc = sum(1 for s in subs if s["included"] == 1)
    revs = list(c.execute(
        "SELECT version, document_version, mutation_type, created_at FROM node_revisions WHERE project_id = ? ORDER BY created_at",
        (pid,)))
    compiles = [r for r in revs if (r["mutation_type"] or "") == "compile"]
    last_compile = compiles[-1]["created_at"] if compiles else None
    jdf = c.execute("SELECT updated_at, length(tree_json) FROM jdf_documents WHERE project_id = ?", (pid,)).fetchone()
    gate = c.execute("SELECT last_compiled_json FROM projects WHERE id = ?", (pid,)).fetchone()
    gate_ps = None
    if gate and gate[0]:
        try:
            gate_ps = (json.loads(gate[0]) or {}).get("gate", {}).get("provenance_stats")
        except Exception:
            gate_ps = "unparseable"
    print("\n--- %s ---" % pid)
    print("  title=%r created=%s updated=%s current_version=%s" % (p["title"], p["created_at"], p["updated_at"], p["current_version"]))
    print("  substrate_rows=%d  included_rows=%d" % (nsub, ninc))
    for s in subs:
        print("    src id=%s incl=%s size=%s tlen=%s file=%r created=%s" % (
            s["id"], s["included"], s["file_size_bytes"], s["tlen"], s["filename"], s["created_at"]))
    print("  node_revisions=%d  compile_revisions=%d  distinct_versions=%s" % (
        len(revs), len(compiles), sorted({r["version"] for r in revs})))
    print("  last_compile_at=%s" % last_compile)
    print("  compile_revision_rows=%s" % [(r["version"], r["document_version"], r["created_at"]) for r in compiles])
    print("  jdf_document updated_at=%s tree_len=%s" % ((jdf["updated_at"], jdf[1]) if jdf else (None, None)))
    print("  last_gate.provenance_stats=%s" % json.dumps(gate_ps))
    for r in c.execute("SELECT created_at, success, duration_ms, error_message FROM audit_log WHERE project_id = ? AND action='DRAFT_STREAM' ORDER BY created_at DESC LIMIT 5", (pid,)):
        print("    audit %s success=%s dur=%s err=%s" % (r["created_at"], r["success"], r["duration_ms"], str(r["error_message"])[:90]))

print("\n=== other titles present (non-demo) ===")
for r in c.execute("SELECT title, count(*) n FROM projects GROUP BY title ORDER BY n DESC"):
    print("  %-28s %d" % (r["title"], r["n"]))
