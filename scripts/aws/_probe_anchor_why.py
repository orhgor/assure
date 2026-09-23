"""Why does the standalone citation line not anchor? Runs the real matcher on it
and on the three figure paragraphs, against the real fixture text."""
import itertools, sqlite3, sys
sys.path.insert(0, "/home/ubuntu/assure-prototype")
from prompt_matrix.models.jdf import (
    attach_substrate_provenance_to_tree, _split_sentences, _tokenize,
    _MIN_ANCHOR_OVERLAP, _MIN_ANCHOR_COEFFICIENT, _MAX_ANCHOR_WINDOW,
)

c = sqlite3.connect("prompt_matrix/history.sqlite")
row = dict(zip(("id", "filename", "extracted_text"),
               c.execute("select id, filename, extracted_text from substrate_vault where id=?",
                         ("sub-d3eab1f0fa9c486d",)).fetchone()))
print("MIN_ANCHOR_OVERLAP=%s MIN_ANCHOR_COEFFICIENT=%s MAX_WINDOW=%s"
      % (_MIN_ANCHOR_OVERLAP, _MIN_ANCHOR_COEFFICIENT, _MAX_ANCHOR_WINDOW))

print("\n-- source sentence candidates (>= %d content tokens) --" % _MIN_ANCHOR_OVERLAP)
elig = [(s, _tokenize(s)) for s, _p in _split_sentences(row["extracted_text"])]
for s, t in elig:
    if len(t) >= _MIN_ANCHOR_OVERLAP:
        print("   %2d tok | %r" % (len(t), s[:88]))
print("  headings that are BELOW the floor (can never anchor):")
for s, t in elig:
    if 0 < len(t) < _MIN_ANCHOR_OVERLAP and len(s) < 40:
        print("   %2d tok | %r" % (len(t), s))

PARAS = [
    ("citation-line (the unanchored one)",
     "Citation: Section 3, Wind and hail deductible"),
    ("figure paragraph: wind/hail",
     "The wind/hail deductible for properties in coastal and high-wind exposure zones (Suffolk, Norfolk, and Essex counties) is 2 percent of the insured value at each location."),
    ("figure paragraph: liability",
     "The maximum general liability per occurrence is $2,000,000 USD, unless a senior underwriter approves a documented exception."),
    ("figure paragraph: inspection",
     "Physical inspection of occupied commercial properties is required at least once every 24 months."),
]

print("\n-- matcher verdict per paragraph --")
for label, text in PARAS:
    doc = {"type": "document", "body": [
        {"type": "section", "id": "sec-1", "title": "t", "children": [
            {"type": "paragraph", "id": "para-1", "content": text}]}]}
    try:
        out = attach_substrate_provenance_to_tree(doc, [], [row])
    except Exception as exc:
        print("  %-36s MATCHER ERROR %s: %s" % (label, type(exc).__name__, exc)); continue
    para = None
    stack = [out]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            if n.get("id") == "para-1":
                para = n
            stack.extend(n.get("children") or [])
    prov = (para or {}).get("provenance") or []
    toks = _tokenize(text)
    print("  %-36s tokens=%d anchored=%s" % (label, len(toks), bool(prov)))
    if prov:
        print("       quote=%r" % str(prov[0].get("extracted_quote"))[:130])
    else:
        print("       tokens=%s" % sorted(toks))
