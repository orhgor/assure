"""Extract the draft text and stats of the last cached compile, for the anchor diagnostic.

Reads the `ast:<project>:<digest>` pipeline_cache payload (what the route stores at the
end of a compile) and writes its draft text where _probe_anchor_diag.py can read it.
Read-only.
"""
import json
import sqlite3
import sys

pid = sys.argv[1] if len(sys.argv) > 1 else "demo-3235f5"
out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/phase_b_draft.txt"

db = sqlite3.connect("prompt_matrix/history.sqlite")
rows = db.execute(
    "select cache_key, updated_at, payload_json from pipeline_cache"
    " where project_id=? and kind='ast' order by updated_at desc", (pid,)).fetchall()
print("ast cache rows for %s: %d" % (pid, len(rows)))
if not rows:
    sys.exit("no cached compile to read")

key, updated, payload = rows[0]
data = json.loads(payload)
compiled = data.get("compiled") or {}
verified = data.get("verified") or {}
draft = compiled.get("draft_text") or ""
print("cache_key=%s updated_at=%s" % (key, updated))
print("draft chars: %d" % len(draft))
print("compiled node_count=%s" % compiled.get("node_count"))
print("verified provenance_stats: %s" % json.dumps(verified.get("provenance_stats")))
open(out, "w").write(draft)
print("wrote %s" % out)
print("\n--- draft text ---")
print(draft)
