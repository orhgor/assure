# The `/ground` path is removed

## What was removed

* `POST /api/projects/<project_id>/nodes/<node_id>/ground` (`routers/ground_routes.py`)
  and its engine `services/ground_node.py`.
* The shell's only affordance for it: the `z3`-violation branch of
  `renderEvidenceFooter` and `performGrounding` (`prototype/shell.js`).
* Its tests (`tests/test_ground_routes.py`).

`search_brave_web` — the one function that fetched rather than rewrote — moved to
`services/web_retrieval.py`, which is the 2C retrieval path's own client.

## Why

> The `/ground` path rewrote unanchored claims to look grounded. Removed because
> it contradicts the product's disclosure model. If a user wants to fix an
> unanchored claim, Surgical Edit is the path — user-initiated, verifiable,
> versioned.

The path searched the web, handed the snippets to a model and persisted the
model's rewrite of the paragraph, stamping `meta.search_attribution`. It created
no source row, ingested nothing, and never re-ran the anchoring gate, so a claim
it "grounded" was still supported by nothing — it had only stopped looking
unanchored. The About page states the opposite: a claim that cannot be grounded
in the source is flagged, never hidden.

2C (`retrieval_routes.py`) is the path that replaced it and the one that owns
unanchored claims: it searches the authoritative domains, fetches a page the user
chooses, stores it as a source row tagged `fetched_url: <host>`, re-runs the
anchoring gate and then the entailment check — so the claim either anchors and is
verified, or it stays flagged.

## Verification

A scratch project compiled with an unanchored claim: the claim stays unanchored
and flagged, and no rewrite is persisted. The anchored counts are unchanged by
the removal — the compile pipeline never called `ground_node` (its only caller was
the removed route), so nothing that produced an anchored paragraph was lost.
