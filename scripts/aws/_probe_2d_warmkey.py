"""2D — recover the demo's warm ask, then prove the memo key is unchanged.

The compile cache stores no ask text, so the only way to exercise the demo's warm
path without guessing at its document is to reconstruct the key: the key is a pure
function of (project, ask, sources, model), and the stored keys are known. This
probe validates the formula on a project whose ask IS known (the scratch project),
then tests candidate asks for the demo against the keys the demo's warm entries
actually carry.

Read-only: it computes keys and compares strings; it compiles nothing.
"""

from __future__ import annotations

import json
import sqlite3
import sys

sys.path.insert(0, "/home/ubuntu/assure-prototype")
sys.path.insert(0, "/home/ubuntu/assure-prototype/prompt_matrix")

from prompt_matrix.db.substrate_repository import list_substrate_for_project  # noqa: E402
from prompt_matrix.routers.draft import (  # noqa: E402
    _build_substrate_context,
    _compile_cache_key,
    _draft_route_model,
    choose_shape,
)

DB = "prompt_matrix/history.sqlite"
MODEL = _draft_route_model(None)
print("compile model:", MODEL, " shape of a memo ask:",
      choose_shape("Summarize the coverage limits"))

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row


def ast_keys(project_id: str) -> list[str]:
    return [
        r["cache_key"]
        for r in c.execute(
            "SELECT cache_key FROM pipeline_cache WHERE project_id = ? AND kind = 'ast' "
            "ORDER BY updated_at DESC",
            (project_id,),
        )
    ]


def key_for(project_id: str, ask: str, context: str | None = None) -> str:
    rows = [r for r in list_substrate_for_project(project_id, with_text=True) if r.get("included")]
    return _compile_cache_key(
        project_id, ask, context, _build_substrate_context(rows), MODEL
    )


# --- 1. the formula, validated against a known ask ---------------------------
scratch = "2d-shape-probe"
known_ask = "Summarize the coverage limits"
scratch_keys = ast_keys(scratch)
computed = key_for(scratch, known_ask)
print("FORMULA scratch computed=%s stored=%s MATCH=%s" % (computed, scratch_keys, computed in scratch_keys))

# --- 2. candidate asks for the demo -----------------------------------------
demo_keys = ast_keys("demo-3235f5")
print("DEMO warm ast keys:", demo_keys)
candidates = [
    "Summarize the Massachusetts commercial real estate underwriting obligations in the source. "
    "Report the wind/hail deductible percentage, the maximum liability in USD, and the inspection "
    "interval in months. Cite each figure.",
    "Summarize the Massachusetts commercial real estate underwriting obligations in the source. "
    "Report the wind/hail deductible percentage, the maximum liability in USD, and the inspection "
    "interval in months.",
    "Summarize the underwriting obligations for Boston commercial property insurance.",
]
for ask in candidates:
    for context in (None, ""):
        key = key_for("demo-3235f5", ask, context)
        print(
            "CANDIDATE %-70s ctx=%-5r %s %s"
            % (ask[:70], context, key, "MATCH" if key in demo_keys else "")
        )

# --- 3. what the warm entries actually hold ---------------------------------
for row in c.execute(
    "SELECT cache_key, payload_json, updated_at FROM pipeline_cache "
    "WHERE project_id = ? AND kind = 'ast' ORDER BY updated_at DESC",
    ("demo-3235f5",),
):
    payload = json.loads(row["payload_json"] or "{}")
    draft = str((payload.get("compiled") or {}).get("draft_text") or "")
    print("WARM %s updated=%s draft=%d chars head=%r" % (
        row["cache_key"], row["updated_at"], len(draft), draft[:90]
    ))
