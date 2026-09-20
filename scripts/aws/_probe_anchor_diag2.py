"""B3 diagnostics — per paragraph, the best window with and without the matcher's guards.

Same inputs as the route (the vault rows it fetches, the same document builder) and
the same floors. For each claim paragraph it prints:

  * the best candidate that PASSES the guards (number-coverage, overlap floor,
    coefficient floor) — i.e. what the matcher actually anchored to;
  * the best candidate with the guards OFF, and why the guarded one lost.

Read-only: never writes a revision.

usage: _probe_anchor_diag2.py <project_id> <draft-file> [substrate_id ...]
"""
import json
import sys

from prompt_matrix.db.substrate_repository import fetch_substrate_entries_by_ids
from prompt_matrix.models.jdf import (
    _MIN_ANCHOR_COEFFICIENT,
    _MIN_ANCHOR_OVERLAP,
    _MIN_CLAIM_TOKENS,
    _numbers,
    _split_sentences,
    _tokenize,
    build_document_from_draft,
    document_to_dict,
    flatten_nodes,
)

pid, draft_path = sys.argv[1], sys.argv[2]
subs = sys.argv[3:]
draft = open(draft_path).read()
rows = fetch_substrate_entries_by_ids(pid, subs) if subs else []

sentences = []  # (row_idx, sent_idx, text, toks, page, nums)
for ri, row in enumerate(rows):
    text = str(row.get("extracted_text") or "")
    for si, (sent, page) in enumerate(_split_sentences(text)):
        toks = _tokenize(sent)
        if len(toks) >= _MIN_ANCHOR_OVERLAP:
            sentences.append((ri, si, sent, toks, page, _numbers(sent)))

print("floors: overlap>=%d coefficient>=%.2f claim_tokens>=%d" % (
    _MIN_ANCHOR_OVERLAP, _MIN_ANCHOR_COEFFICIENT, _MIN_CLAIM_TOKENS))
print("source sentences considered: %d" % len(sentences))

by_row = {}
for idx, s in enumerate(sentences):
    by_row.setdefault(s[0], []).append(idx)

doc = document_to_dict(build_document_from_draft(pid, draft))
anchored, unanchored = [], []
for node in flatten_nodes(doc):
    if node.get("type") != "paragraph":
        continue
    content = str(node.get("content") or "").strip()
    if not content:
        continue
    ctoks = _tokenize(content)
    if len(ctoks) < _MIN_CLAIM_TOKENS:
        continue
    cnums = _numbers(content)
    label = f"{node.get('id')} [{len(ctoks)} toks, numbers={sorted(cnums)}]"
    print("\n== %s" % label)
    print("   claim: %s" % " ".join(content.split())[:200])

    cands = []
    for ri, si, sent, toks, page, nums in sentences:
        inter = len(ctoks & toks)
        cov = not (cnums - nums)
        cands.append({"kind": "single", "si": si, "inter": inter, "n": len(toks),
                      "coeff": inter / min(len(ctoks), len(toks)) if toks else 0.0,
                      "cov": cov, "text": sent})
    for ri, idxs in by_row.items():
        for a in range(len(idxs)):
            for w in (2, 3, 4):
                grp = idxs[a:a + w]
                if len(grp) < w:
                    continue
                union, unums = set(), set()
                for g in grp:
                    union |= sentences[g][3]
                    unums |= sentences[g][5]
                inter = len(ctoks & union)
                cands.append({"kind": "w%d" % w,
                              "span": [sentences[grp[0]][1], sentences[grp[-1]][1]],
                              "inter": inter, "n": len(union),
                              "coeff": inter / min(len(ctoks), len(union)) if union else 0.0,
                              "cov": not (cnums - unums),
                              "text": " ".join(sentences[g][2] for g in grp)})

    guarded = [c for c in cands if c["cov"] and c["inter"] >= _MIN_ANCHOR_OVERLAP
               and c["coeff"] >= _MIN_ANCHOR_COEFFICIENT]
    guarded.sort(key=lambda c: -c["coeff"])
    loose = sorted(cands, key=lambda c: -c["coeff"])
    best_loose = loose[0] if loose else None

    if guarded:
        b = guarded[0]
        print("   ANCHORED  %s %s inter=%d/min(%d,%d) coeff=%.3f" % (
            b["kind"], b.get("span", b.get("si")), b["inter"], len(ctoks), b["n"], b["coeff"]))
        print("      %s" % " ".join(b["text"].split())[:200])
        anchored.append(node.get("id"))
    else:
        print("   UNANCHORED — no candidate cleared the guards")
        unanchored.append((node.get("id"), cnums, best_loose))
        if best_loose:
            print("     best window with guards OFF: %s %s inter=%d/min(%d,%d) coeff=%.3f cov=%s" % (
                best_loose["kind"], best_loose.get("span", best_loose.get("si")),
                best_loose["inter"], len(ctoks), best_loose["n"], best_loose["coeff"], best_loose["cov"]))
            print("        %s" % " ".join(best_loose["text"].split())[:220])
        # why: coverage, floor, or coefficient
        cov_ok = [c for c in cands if c["cov"]]
        if not cov_ok:
            print("     reason: NO candidate covers the claim's numbers %s (number guard rejects every window)" % sorted(cnums))
        else:
            best_cov = max(cov_ok, key=lambda c: c["coeff"])
            print("     reason: best number-covering candidate coeff=%.3f below floor %.2f (or overlap < %d)" % (
                best_cov["coeff"], _MIN_ANCHOR_COEFFICIENT, _MIN_ANCHOR_OVERLAP))
            print("        %s" % " ".join(best_cov["text"].split())[:220])

print("\n=== summary ===")
print("anchored paragraphs:   %d -> %s" % (len(anchored), anchored))
print("unanchored paragraphs: %d -> %s" % (len(unanchored), [u[0] for u in unanchored]))
