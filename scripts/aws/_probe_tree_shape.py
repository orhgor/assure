"""Introspect the persisted document tree: body shape, node types, provenance keys."""
import json
import sqlite3

c = sqlite3.connect("prompt_matrix/history.sqlite")
row = c.execute("select version, jdf_tree from jdf_revisions where project_id='demo-3235f5' order by version desc limit 1").fetchone()
print("latest revision version=%s" % row[0])
tree = json.loads(row[1])
print("top-level keys:", sorted(tree.keys()))
body = tree.get("body") or []
print("body len:", len(body))
for i, s in enumerate(body):
    print("\n[%d] type=%s keys=%s" % (i, s.get("type"), sorted(s.keys())))
    print("    content: %s" % " ".join(str(s.get("content") or "").split())[:90])
    print("    provenance: %s" % json.dumps(s.get("provenance"))[:200])
    print("    meta keys: %s" % sorted((s.get("meta") or {}).keys()))
    ch = s.get("children") or []
    print("    children: %d" % len(ch))
    for j, n in enumerate(ch):
        prov = n.get("provenance") or []
        print("      (%d) type=%s id=%s prov_rows=%d prov_keys=%s" % (
            j, n.get("type"), n.get("id"), len(prov) if isinstance(prov, list) else -1,
            sorted(prov[0].keys()) if isinstance(prov, list) and prov and isinstance(prov[0], dict) else None))
        if isinstance(prov, list) and prov and isinstance(prov[0], dict):
            print("          anchor_window: %s" % str(prov[0].get("anchor_window"))[:120])
            print("          extracted_quote: %s" % str(prov[0].get("extracted_quote"))[:120])
        print("          meta.provenance: %s" % json.dumps((n.get("meta") or {}).get("provenance"))[:200])
