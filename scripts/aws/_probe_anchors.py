"""Print the persisted anchors + verdicts of a project's current document.
usage: _probe_anchors.py <project_id> [version]
"""
import json, sqlite3, sys

pid = sys.argv[1]
c = sqlite3.connect("prompt_matrix/history.sqlite")
row = c.execute(
    "select version, jdf_tree from jdf_revisions where project_id=? order by version desc limit 1",
    (pid,)).fetchone()
if not row:
    print("no revisions")
    raise SystemExit(0)
version, raw = row
doc = json.loads(raw) if isinstance(raw, str) else raw
print(f"project={pid} version={version}")
for section in doc.get("body") or []:
    for node in [section, *(section.get("children") or [])]:
        if not isinstance(node, dict) or node.get("type") != "paragraph":
            continue
        prov = node.get("provenance") or []
        ent = ((node.get("meta") or {}).get("provenance") or {}).get("entailment") or {}
        print(f"\n-- {node.get('id')} prov_rows={len(prov)} verdict={ent.get('verdict')!r}")
        print(f"   claim : {str(node.get('content') or '')[:200]}")
        for p in prov:
            print(f"   span  : {p.get('anchor_window_span')!r}")
            print(f"   quote : {p.get('extracted_quote')!r}")
            print(f"   window: {str(p.get('anchor_window'))[:260]!r}")
        if ent:
            print(f"   reason: {ent.get('reasoning')!r}")
