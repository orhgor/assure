"""A/B the entailment check: single quoted sentence vs the anchor window.

usage: _probe_entail_ab.py <project_id> [max_paragraphs]

Reads the project's current document, and for each anchored paragraph calls the real
SEMANTIC_VALIDATION model twice — once with extracted_quote, once with anchor_window —
printing both verdicts, both reasons, and the input tokens each call was billed.
"""
import json, sqlite3, sys

from prompt_matrix.services.entailment import check_entailment

pid = sys.argv[1]
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 3
PROBE_PROJECT = "wave2c-probe"

db = sqlite3.connect("prompt_matrix/history.sqlite")
raw = db.execute("select jdf_tree from jdf_revisions where project_id=? order by version desc limit 1",
                 (pid,)).fetchone()[0]
doc = json.loads(raw) if isinstance(raw, str) else raw

rows = []
for section in doc.get("body") or []:
    for node in [section, *(section.get("children") or [])]:
        if not isinstance(node, dict) or node.get("type") != "paragraph":
            continue
        claim = str(node.get("content") or "").strip()
        prov = node.get("provenance") or []
        quote = window = ""
        for p in prov:
            if isinstance(p, dict):
                quote = str(p.get("extracted_quote") or "").strip()
                window = str(p.get("anchor_window") or "").strip()
                if quote or window:
                    break
        if claim and quote:
            rows.append({"id": node.get("id"), "claim": claim, "quote": quote,
                         "window": window or quote})

print(f"anchored paragraphs in {pid}: {len(rows)}")

def tokens_of(text):
    for r in db.execute(
        "select input_tokens from token_ledger_entries where project_id=? and task_type='semantic_validation'"
        " order by rowid desc limit 1", (PROBE_PROJECT,)):
        return int(r[0])
    return None

for row in rows[:limit]:
    print("\n" + "=" * 70)
    print("node:", row["id"])
    print("claim :", row["claim"][:220])
    print("quote :", row["quote"][:200])
    print("window:", row["window"][:300])
    for label, text in (("single-sentence (old source)", row["quote"]),
                        ("anchor window (new source)", row["window"])):
        rec = check_entailment(row["claim"], text, project_id=PROBE_PROJECT)
        print(f"  [{label}] verdict={rec['verdict']!r} input_tokens={tokens_of(text)}")
        print(f"      reason: {rec['reasoning']}")
