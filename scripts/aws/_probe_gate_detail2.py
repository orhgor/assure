import json, glob, os

def walk(node, out):
    if isinstance(node, dict):
        if node.get("type") in ("paragraph", "heading", "p", "text"):
            out.append(node)
        for ch in node.get("children") or []:
            walk(ch, out)
    elif isinstance(node, list):
        for ch in node:
            walk(ch, out)

for path in sorted(glob.glob("/tmp/phase_b_run*_verified.json"), key=lambda p: int("".join(c for c in os.path.basename(p) if c.isdigit()))):
    ver = json.load(open(path))
    ps = ver.get("provenance_stats") or {}
    print("=" * 78)
    print(os.path.basename(path), json.dumps(ps))
    nodes = []
    walk(ver.get("document") or {}, nodes)
    print("  paragraph-ish nodes: %d" % len(nodes))
    for i, n in enumerate(nodes):
        txt = str(n.get("content") or n.get("text") or "")
        prov = n.get("provenance") or []
        q = [p.get("extracted_quote") for p in prov if isinstance(p, dict) and p.get("extracted_quote")]
        print("   [%d] type=%-9s len=%-4d anchored=%-5s" % (i, n.get("type"), len(txt), bool(q)))
        print("       %r" % txt[:180])
        if q:
            print("       q=%r" % str(q[0])[:140])
    claims = ver.get("claims")
    if claims:
        print("  claims payload: %s" % json.dumps(claims)[:1500])
