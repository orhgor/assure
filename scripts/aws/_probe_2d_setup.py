"""2D baseline (2D.2) — a scratch project with the demo's own source, no demo writes.

Creates `2d-shape-probe` if missing, copies the demo's substrate text into it, and
prints the ids the compile probes need. Read-only against demo-3235f5.
"""
import sqlite3
import sys

sys.path.insert(0, "/home/ubuntu/assure-prototype")
sys.path.insert(0, "/home/ubuntu/assure-prototype/prompt_matrix")

from prompt_matrix.db.jdf_repository import ensure_project  # noqa: E402
from prompt_matrix.db.substrate_repository import save_substrate_entry  # noqa: E402

DEMO = "demo-3235f5"
PROJECT = "2d-shape-probe"

c = sqlite3.connect("prompt_matrix/history.sqlite")
row = c.execute(
    "SELECT id, filename, extracted_text FROM substrate_vault WHERE project_id = ? ORDER BY created_at LIMIT 1",
    (DEMO,),
).fetchone()
if not row:
    raise SystemExit("demo has no substrate row")

demo_src_id, filename, text = row
print("demo source:", demo_src_id, filename, len(text), "chars")

ensure_project(PROJECT, "2D shape probe")
own = c.execute(
    "SELECT id FROM substrate_vault WHERE project_id = ? AND filename = ? LIMIT 1",
    (PROJECT, filename),
).fetchone()
target_id = own[0] if own else "sub-2dshape00000001"
if not own:
    result = save_substrate_entry(
        PROJECT,
        filename=filename,
        page_count=1,
        extracted_text=text,
        entry_id=target_id,
    )
    print("copied source:", result)
print("PROJECT=%s SUBSTRATE=%s" % (PROJECT, target_id))
