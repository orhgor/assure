"""Which paragraph is unanchored, and what does it claim? Reads the verified
frames the five-run probe left in /tmp."""
import json, glob, os

for path in sorted(glob.glob("/tmp/phase_b_run*_verified.json"), key=lambda p: int("".join(c for c in os.path.basename(p) if c.isdigit()))):
    try:
        ver = json.load(open(path))
    except Exception as exc:
        print(path, "unreadable:", exc); continue
    print("=" * 78)
    print(os.path.basename(path), " keys:", sorted(ver.keys()))
    ps = ver.get("provenance_stats") or {}
    print("  stats:", json.dumps(ps))
    doc = ver.get("document") or ver.get("document_body") or {}
    nodes = doc.get("body") if isinstance(doc, dict) else None
    if not isinstance(nodes, list):
        print("  document keys:", list(doc.keys()) if isinstance(doc, dict) else type(doc))
        nodes = []
    print("  nodes:", len(nodes))
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            continue
        txt = str(n.get("content") or n.get("text") or "")
        prov = n.get("provenance") or []
        quotes = [p.get("extracted_quote") for p in prov if isinstance(p, dict) and p.get("extracted_quote")]
        ent = n.get("entailment") or {}
        print("   [%d] type=%s len=%d anchored=%s ent=%s" % (
            i, n.get("type"), len(txt), bool(quotes), ent.get("verdict")))
        print("       text=%r" % txt[:200])
        if quotes:
            print("       quote=%r" % str(quotes[0])[:160])
        else:
            print("       quote=NONE  keys=%s" % sorted(n.keys()))
