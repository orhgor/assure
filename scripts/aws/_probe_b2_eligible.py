"""B2: show which source sentences the eligible filter drops, on a real source.

usage: _probe_b2_eligible.py <substrate_id>

Reads the source's extracted_text from the SQLite vault, runs the real
_split_sentences / _tokenize / _MIN_ANCHOR_OVERLAP, and prints the sentences
that fall below the overlap floor — the ones that can never be part of an
anchor window, because windows are built only from the eligible list.
"""
import sqlite3
import sys

sys.path.insert(0, ".")
from prompt_matrix.models.jdf import (  # noqa: E402
    _MIN_ANCHOR_OVERLAP,
    _split_sentences,
    _tokenize,
)

DB = "prompt_matrix/history.sqlite"
sid = sys.argv[1]

con = sqlite3.connect(DB)
row = con.execute(
    "SELECT extracted_text FROM substrate_vault WHERE id=?", (sid,)
).fetchone()
text = row[0] if row else ""
print(f"source {sid}: {len(text)} chars")
print(f"_MIN_ANCHOR_OVERLAP = {_MIN_ANCHOR_OVERLAP}")

sents = _split_sentences(text)
eligible = []
dropped = []
for sent, page in sents:
    toks = _tokenize(sent)
    (eligible if len(toks) >= _MIN_ANCHOR_OVERLAP else dropped).append((sent, toks, page))

print(f"\nsentences total={len(sents)}  eligible={len(eligible)}  dropped={len(dropped)}")
print("\n--- DROPPED (never an anchor window, and never a window neighbour) ---")
for sent, toks, page in dropped:
    print(f"  [{len(toks)} tok, p{page}] {sent[:110]!r}")

print("\n--- ELIGIBLE ---")
for sent, toks, page in eligible[:12]:
    print(f"  [{len(toks)} tok, p{page}] {sent[:110]!r}")
