"""A/B the entailment prompt: the pre-calibration template vs the deployed one.

usage: _probe_entail_d.py <project_id> <old_prompt_file> [n]

For each anchored paragraph of the project's current document, calls the real
SEMANTIC_VALIDATION model once with the old prompt template and once with the
template the box is serving, then prints both verdicts, both reasons, and the input
tokens each call was billed (from the token ledger).
"""
import json, sqlite3, sys

from prompt_matrix.services import entailment as E

pid = sys.argv[1]
old_path = sys.argv[2]
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 5
PROBE_PROJECT = "wave2d-probe"

OLD = open(old_path).read()
NEW = E._PROMPT
print("old prompt chars:", len(OLD), " new prompt chars:", len(NEW))
print("added sentences present in deployed prompt:",
      "A paraphrase that preserves the substance" in NEW,
      "/", "Supporting detail that names the entities" in NEW)

db = sqlite3.connect("prompt_matrix/history.sqlite")
revisions = [r[0] for r in db.execute(
    "select jdf_tree from jdf_revisions where project_id=? order by version desc limit 12", (pid,))]

rows, seen = [], set()
for raw in revisions:
    doc = json.loads(raw) if isinstance(raw, str) else raw
    for section in doc.get("body") or []:
        for node in [section, *(section.get("children") or [])]:
            if not isinstance(node, dict) or node.get("type") != "paragraph":
                continue
            claim = str(node.get("content") or "").strip()
            prov = node.get("provenance") or []
            window = ""
            for p in prov:
                if isinstance(p, dict) and str(p.get("anchor_window") or "").strip():
                    window = str(p["anchor_window"]).strip()
                    break
            key = claim[:120]
            if claim and window and key not in seen:
                seen.add(key)
                rows.append({"id": node.get("id"), "claim": claim, "window": window})

print(f"anchored paragraphs with a window: {len(rows)}; checking {min(limit, len(rows))}")


def billed_tokens():
    row = db.execute(
        "select input_tokens from token_ledger_entries where project_id=? and task_type='semantic_validation'"
        " order by rowid desc limit 1", (PROBE_PROJECT,)).fetchone()
    return int(row[0]) if row else None


for row in rows[:limit]:
    print("\n" + "=" * 72)
    print("node :", row["id"])
    print("claim:", row["claim"][:240])
    print("window:", row["window"][:300])
    out = {}
    for label, template in (("old", OLD), ("new", NEW)):
        E._PROMPT = template
        rec = E.check_entailment(row["claim"], row["window"], project_id=PROBE_PROJECT)
        out[label] = rec
        print(f"  [{label}] verdict={rec['verdict']!r} input_tokens={billed_tokens()}")
        print(f"      reason: {rec['reasoning']}")
    print(f"  MOVED: {out['old']['verdict']} -> {out['new']['verdict']}"
          if out["old"]["verdict"] != out["new"]["verdict"] else
          f"  unchanged: {out['old']['verdict']}")
