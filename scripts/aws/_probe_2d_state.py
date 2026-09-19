"""2D0 — read the persisted state the JDF sidecar has to carry, for one project.

Read-only: jdf_revisions (the version chain), jdf_documents (legacy), the gate block
in projects.last_compiled_json (drafting model + provenance stats), and the node-level
provenance/verification state of the live tree.
"""
import json
import sqlite3
import sys

sys.path.insert(0, "/home/ubuntu/assure-prototype")
sys.path.insert(0, "/home/ubuntu/assure-prototype/prompt_matrix")
DB = "prompt_matrix/history.sqlite"
pid = sys.argv[1] if len(sys.argv) > 1 else "demo-3235f5"

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

print("=== jdf_revisions (version chain) ===")
for r in c.execute(
    "SELECT version, mutation_type, target_node_id, change_summary, created_at, length(jdf_tree) AS n "
    "FROM jdf_revisions WHERE project_id = ? ORDER BY version",
    (pid,),
):
    print(dict(r))

print("=== jdf_documents (legacy) ===")
for r in c.execute(
    "SELECT document_id, updated_at, length(tree_json) AS n FROM jdf_documents WHERE project_id = ?", (pid,)
):
    print(dict(r))

row = c.execute("SELECT last_compiled_json, source_md FROM projects WHERE id = ?", (pid,)).fetchone()
data = {}
if row and row[0]:
    try:
        data = json.loads(row[0]) or {}
    except Exception as exc:
        print("unparseable last_compiled_json:", exc)
print("=== last_compiled_json keys:", sorted(data.keys()))
print("gate:", json.dumps(data.get("gate"), indent=1)[:1200])
print("measure:", json.dumps((data.get("gate") or {}).get("measure")))

print("=== audit_log DRAFT_STREAM (latest 3) ===")
for r in c.execute(
    "SELECT created_at, details FROM audit_log WHERE project_id = ? AND action='DRAFT_STREAM' "
    "ORDER BY created_at DESC LIMIT 3",
    (pid,),
):
    print(r["created_at"], str(r["details"])[:300])

from prompt_matrix.db.jdf_repository import fetch_latest_jdf_or_empty  # noqa: E402

tree = fetch_latest_jdf_or_empty(pid)
print("=== tree ===")
print("document_id:", tree.get("document_id"), "meta:", json.dumps(tree.get("meta"))[:300])
print("truth_ledger:", json.dumps(tree.get("truth_ledger"))[:300])
for section in tree.get("body") or []:
    for node in [section] + list(section.get("children") or []):
        if not isinstance(node, dict):
            continue
        meta = node.get("meta") or {}
        print(
            "  %-10s %-14s content=%r" % (
                node.get("type"),
                str(node.get("id"))[:14],
                str(node.get("content") or node.get("title") or "")[:70],
            )
        )
        print(
            "        provenance_rows=%d meta.provenance=%s ann.z3=%d spans=%d" % (
                len(node.get("provenance") or []),
                json.dumps(meta.get("provenance"))[:400],
                len((node.get("annotations") or {}).get("z3") or []),
                len(meta.get("confidenceSpans") or []),
            )
        )
