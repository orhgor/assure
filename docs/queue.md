# Queue

```
FROZEN QUEUE (next 6)

| # | Item | Type | Blocker | Estimate gate | Done when |
|---|------|------|---------|---------------|-----------|
| 1 | Fix free-model / DeepSeek auth (live compile returns real output) | Bug | correct DeepSeek credential on staging (which key — UNKNOWN) | which provider/key the free stack should use | a live compile renders node text != error; prototype_surgical NODE_CONTAINS_NEW_TEXT=true |
| 2 | JDF search→edit bridge (Path B) | Wire | none (design clear: render result as .jdf-node, call _attachNodeRephrase shell.js:1097) | whether a result loads the full JDF doc or a single node | prototype_surgical_from_search reaches the editor and passes its SEARCH→EDIT assertion |
| 3 | Per-route auth + staging ownership enforcement (I1) | Build/Design | agreed auth model for dev vs staging | agreed dev-bypass policy | unauthenticated /api/projects/<id>/* returns 401 on staging; prod-gated |
| 4 | Source isolation + provenance (I2/I3) | Design | product decision on source_kind + retention/erase | retention/erase policy | chunks carry source_kind, search filters by tenant, doc-level delete exists |
| 5 | Deploy silent-success fix (verify app serves new code) | Bug/Build | none | no unknown — est 4h | a post-deploy curl of the new route must 200 before the run reports success; fail otherwise |
| 6 | All E2E green on staging (retarget golden_path off /app; content asserts) | Test | items 1,2 | depends on 1,2 | prototype_shell, prototype_jdf, prototype_surgical, prototype_surgical_from_search all pass |

AFTER V1 (titles only):
  - FlowX OpenCover integration
  - gdpr-officer integration
  - LightningParse integration
  - Semantic embedding (Ollama/OpenAI)
  - Unify the two JDF converters (PyMuPDF + jdf-cli)
  - Rate limiting on ingest
  - Source-level erase/retention policies
```
