"""Diagnose the anchoring matcher against a draft text.

usage: _probe_anchor_diag.py <project_id> <draft-file> [substrate_id ...]

Replicates the pipeline's anchoring inputs exactly (the same substrate rows the route
fetches, the same document builder) and prints, per paragraph, the best single-sentence
candidate and the best sliding-window candidate with their token sets and coefficients.
Read-only: it never writes a revision.
"""
import json, sys

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
print("substrate rows:", [(r.get("id"), r.get("filename"), len(str(r.get("extracted_text") or ""))) for r in rows])

sentences = []          # (row_idx, sent_idx, text, toks, page, nums)
for ri, row in enumerate(rows):
    text = str(row.get("extracted_text") or "")
    for si, (sent, page) in enumerate(_split_sentences(text)):
        toks = _tokenize(sent)
        if len(toks) >= _MIN_ANCHOR_OVERLAP:
            sentences.append((ri, si, sent, toks, page, _numbers(sent)))
print("eligible source sentences:", len(sentences))
print("floors: overlap>=", _MIN_ANCHOR_OVERLAP, " coefficient>=", _MIN_ANCHOR_COEFFICIENT,
      " claim tokens>=", _MIN_CLAIM_TOKENS)

doc = document_to_dict(build_document_from_draft(pid, draft))
for node in flatten_nodes(doc):
    if node.get("type") != "paragraph":
        continue
    content = str(node.get("content") or "").strip()
    if not content:
        continue
    ctoks = _tokenize(content)
    if len(ctoks) < _MIN_CLAIM_TOKENS:
        print(f"\n-- {node.get('id')}: SKIP (< {_MIN_CLAIM_TOKENS} claim tokens) :: {content[:80]}")
        continue
    cnums = _numbers(content)
    print(f"\n== {node.get('id')} ({len(ctoks)} tokens, numbers={sorted(cnums)})")
    print(f"   claim: {content[:220]}")

    best1 = None
    win_rows = []
    for ri, si, sent, toks, page, nums in sentences:
        if cnums - nums:
            continue
        inter = len(ctoks & toks)
        if inter < _MIN_ANCHOR_OVERLAP:
            continue
        score = inter / min(len(ctoks), len(toks))
        if best1 is None or score > best1["score"]:
            best1 = {"score": score, "ri": ri, "si": si, "inter": inter,
                     "ntoks": len(toks), "text": sent}
    # windows of 2 and 3 consecutive sentences from the same row
    by_row = {}
    for idx, s in enumerate(sentences):
        by_row.setdefault(s[0], []).append(idx)
    for ri, idxs in by_row.items():
        for a in range(len(idxs)):
            for w in (2, 3):
                grp = idxs[a:a + w]
                if len(grp) < w:
                    continue
                union = set()
                unums = set()
                for g in grp:
                    union |= sentences[g][3]
                    unums |= sentences[g][5]
                if cnums - unums:
                    continue
                inter = len(ctoks & union)
                if inter < _MIN_ANCHOR_OVERLAP:
                    continue
                score = inter / min(len(ctoks), len(union))
                win_rows.append({"score": score, "w": w, "span": [sentences[grp[0]][1], sentences[grp[-1]][1]],
                                 "inter": inter, "ntoks": len(union),
                                 "text": " ".join(sentences[g][2] for g in grp)})
    win_rows.sort(key=lambda r: -r["score"])
    if best1:
        print(f"   best SINGLE sentence: idx {best1['si']} inter={best1['inter']}/min({len(ctoks)},{best1['ntoks']}) "
              f"coeff={best1['score']:.3f} {'PASS' if best1['score'] >= _MIN_ANCHOR_COEFFICIENT else 'fail'}")
        print(f"      {best1['text'][:200]}")
    else:
        print("   best SINGLE sentence: none cleared the overlap floor")
    for r in win_rows[:3]:
        print(f"   window w={r['w']} sentences {r['span'][0]}-{r['span'][1]} inter={r['inter']}/min({len(ctoks)},{r['ntoks']}) "
              f"coeff={r['score']:.3f} {'PASS' if r['score'] >= _MIN_ANCHOR_COEFFICIENT else 'fail'}")
        print(f"      {r['text'][:240]}")
