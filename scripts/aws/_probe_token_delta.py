"""Measured input-token cost of the window per entailment call, on the demo fixture.

usage: _probe_token_delta.py
"""
import sqlite3

from prompt_matrix.models.jdf import _MIN_ANCHOR_OVERLAP, _split_sentences, _tokenize
from prompt_matrix.services.entailment import check_entailment

PROBE = "wave2c-tokens"
policy = open("docs/demo/insurance-boston-real-estate/assets/naic-underwriting-policy-redacted.md").read()
# The matcher's candidate unit: sentences carrying >= _MIN_ANCHOR_OVERLAP content tokens.
sentences = [s for s, _p in _split_sentences(policy) if len(_tokenize(s)) >= _MIN_ANCHOR_OVERLAP]
# The deductible claim and the three consecutive sentences a 3-wide window spans
# on this fixture (indices as the matcher numbers them: 4, 5, 6).
claim = "Wind and Hail Deductible Percentage\nFor commercial properties located in coastal and high-wind exposure zones—specifically Suffolk, Norfolk, and Essex counties—the wind and hail deductible is set at 2 percent of the insured value at each location."
window = " ".join(sentences[4:7])  # the matcher's span 4-6 window
print("claim chars:", len(claim))
print("window text:", window[:300])

db = sqlite3.connect("prompt_matrix/history.sqlite")

def billed():
    row = db.execute(
        "select input_tokens from token_ledger_entries where project_id=? and task_type='semantic_validation'"
        " order by rowid desc limit 1", (PROBE,)).fetchone()
    return int(row[0]) if row else None

prev = None
for width in (1, 2, 3):
    source = " ".join(sentences[4 : 4 + width])
    rec = check_entailment(claim, source, project_id=PROBE)
    tok = billed()
    delta = "" if prev is None else f"  (+{tok - prev} vs width {width - 1})"
    print(f"width {width}: source_chars={len(source):4d} input_tokens={tok}{delta}  verdict={rec['verdict']!r}")
    prev = tok
