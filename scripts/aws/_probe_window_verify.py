"""Window-matcher verification: per paragraph, best single sentence vs best window.

usage: _probe_window_verify.py <draft_file> <source_id=path> [<source_id=path> ...]

Runs with the deployed code. The "single" column is the pre-window matcher's answer
(best candidate of width 1 under the same floors and the same numeric check); the
"window" column is the new candidate set. Prints the span (sentence indices), the
token set actually compared, and the coefficient for each.
"""
import re, sys

from prompt_matrix.models.jdf import (
    _MAX_ANCHOR_WINDOW,
    _MIN_ANCHOR_COEFFICIENT,
    _MIN_ANCHOR_OVERLAP,
    _MIN_CLAIM_TOKENS,
    _numbers,
    _split_sentences,
    _tokenize,
    attach_substrate_provenance_to_tree,
    build_document_from_draft,
    document_to_dict,
    flatten_nodes,
)

draft = open(sys.argv[1]).read()
rows = []
for spec in sys.argv[2:]:
    sid, path = spec.split("=", 1)
    rows.append({"id": sid, "filename": path.rsplit("/", 1)[-1], "extracted_text": open(path).read()})

print(f"source rows: {[(r['id'], r['filename']) for r in rows]}")
print(f"floors: overlap>={_MIN_ANCHOR_OVERLAP} coefficient>={_MIN_ANCHOR_COEFFICIENT} "
      f"min_window=1 max_window={_MAX_ANCHOR_WINDOW}")

sentences = []
for ri, row in enumerate(rows):
    for si, (sent, page) in enumerate(_split_sentences(str(row["extracted_text"]))):
        toks = _tokenize(sent)
        if len(toks) >= _MIN_ANCHOR_OVERLAP:
            sentences.append({"ri": ri, "si": si, "text": sent, "toks": toks, "nums": _numbers(sent)})

# what the deployed matcher actually chose
real = {}
doc = document_to_dict(build_document_from_draft("probe", draft))
for node in flatten_nodes(attach_substrate_provenance_to_tree(doc, [], rows)):
    prov = node.get("provenance") or []
    if prov:
        real[node["id"]] = (prov[0].get("anchor_window_span"), prov[0].get("extracted_quote"))

def best_single(ctoks, cnums):
    best = None
    for s in sentences:
        if cnums - s["nums"]:
            continue
        inter = len(ctoks & s["toks"])
        if inter < _MIN_ANCHOR_OVERLAP:
            continue
        score = inter / min(len(ctoks), len(s["toks"]))
        if best is None or score > best["score"]:
            best = {"score": score, "inter": inter, "span": f"{s['si']}-{s['si']}",
                    "toks": s["toks"], "denom": min(len(ctoks), len(s["toks"])), "text": s["text"]}
    return best

def best_window(ctoks, cnums):
    best = None
    for ri in {s["ri"] for s in sentences}:
        idx = [s for s in sentences if s["ri"] == ri]
        for a in range(len(idx)):
            union, unums = set(), set()
            for w in range(1, _MAX_ANCHOR_WINDOW + 1):
                if a + w > len(idx):
                    break
                union = union | idx[a + w - 1]["toks"]
                unums = unums | idx[a + w - 1]["nums"]
                if cnums - unums:
                    continue
                inter = len(ctoks & union)
                if inter < _MIN_ANCHOR_OVERLAP:
                    continue
                score = inter / min(len(ctoks), len(union))
                cand = {"score": score, "inter": inter,
                        "span": f"{idx[a]['si']}-{idx[a + w - 1]['si']}",
                        "toks": union, "denom": min(len(ctoks), len(union)),
                        "text": " ".join(s["text"] for s in idx[a:a + w])}
                if best is None or score > best["score"] or (
                        score == best["score"] and w < best["width"]):
                    cand["width"] = w
                    best = cand
    return best

for node in flatten_nodes(doc):
    if node.get("type") != "paragraph":
        continue
    content = str(node.get("content") or "").strip()
    if not content:
        continue
    ctoks, cnums = _tokenize(content), _numbers(content)
    if len(ctoks) < _MIN_CLAIM_TOKENS:
        continue
    print(f"\n== {node['id']} claim_tokens={len(ctoks)} numbers={sorted(cnums)}")
    print(f"   claim: {content[:180]}")
    bs, bw = best_single(ctoks, cnums), best_window(ctoks, cnums)
    for label, b in (("single", bs), ("window", bw)):
        if b is None:
            print(f"   {label}: NO candidate cleared the floors")
        else:
            mark = "PASS" if b["score"] >= _MIN_ANCHOR_COEFFICIENT else "fail"
            print(f"   {label}: span {b['span']} |{b['inter']}| / min({len(ctoks)},{b['denom']}) "
                  f"= coeff {b['score']:.3f} {mark}")
            print(f"      tokens compared: {sorted(b['toks'])}")
            print(f"      {b['text'][:220]}")
    print(f"   deployed matcher chose: span={real.get(node['id'], ('UNANCHORED',))[0]}")
