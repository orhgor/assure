# Assure — Current State
Last verified: 2026-09-17

## Deployed
- URL: prototype.getassureai.com
- EC2 HEAD: 173ae30 (prototype/shell-skeleton)
- Services: assure-prototype (:8890), assure-prototype-static (:8891)
- JDF binary: /opt/node-v24.11.1-linux-arm64/bin/jdf
- OMP: :3456, keyword-mode
- Both services active, no errors in journal

## Verified live (curl or test)
- /                    200
- /shell.js            200
- /shell.css           200
- /api/health          200
- /api/projects/<id>/jdf/health    200 (JDF_BIN detected)
- /api/projects/<id>/jdf/search    400 (validation: "query required")
- No 500s anywhere
- prototype_shell.spec.js    ✅ passing

## Feature classification
| Feature | Bucket | Evidence |
|---|---|---|
| 29 routers in create_app | Live | web.py:1590-1753 |
| JDF ingest/search/health | Live | jdf_memory_routes.py:29; web.py:1680-1683 |
| JDF persistence API (/import-pdf) | Live | jdf_routes.py:139; web.py:1595-1599 |
| jdf-cli converter (pdf_to_jdf, jdf_to_chunks) | Live | jdf_converter.py:27,45 |
| JDF memory (remember, search) | Live | jdf_memory.py:95,139 |
| OMP client (remember, recall, list, health) | Live | omp_client.py:119,134,209,217 |
| OMP compile cache (safe_*) | Live | omp_memory.py:20-21,91,141,165,192,215,240 |
| Inline rephrase (8083569) | Live | shell.js:1080,1091,1097,1132,1153,2341 |
| Node history (99fb404) | Live | shell.js:822,165,1189,2342 |
| Red-Hat verify stage (f4fc280) | Live | shell.js:2990,3175; wired :1549,3077 |
| JDF ingest UI | Live | index.html:211-216; shell.js:415-504 |
| pdf_bytes_to_jdf (PyMuPDF) | Live | pdf_import.py:42 |
| Cost/token measurement (370513e) | **Hidden** | computed draft.py:427,463; cost_governance.py:138,487-489; UI surfacing not found |
| ~15 services/*.py | **Unclear** | importer/caller not traced |

## Feature flags on staging
| Flag | Value | Effect |
|---|---|---|
| ASSURE_USE_FREE_MODELS | 1 | Free model stack on |
| ENVIRONMENT / ASSURE_ENV | staging | Environment |
| CELERY_TASK_ALWAYS_EAGER | 1 | Celery sync |
| USE_GROUNDRAILS_SERVICE | 0 | Groundrails off |
| PEM_OMP_CACHE | 1 (default) | OMP cache on |
| **ASSURE_ENFORCE_OWNERSHIP** | **off** | **Auth gate disabled** |
| ASSURE_QUIET_START | unclear | — |
| ASSURE_REQUIRE_LOGIN | unclear | — |
| ASSURE_ENABLE_COMPILE_LIMIT | unclear | — |

## Test coverage
| Suite | Status | Proves |
|---|---|---|
| tests/test_jdf_converter.py | ✅ 4 tests | Subprocess wrapping |
| tests/test_jdf_routes.py | ✅ 8 tests | Routes, auth, cap, 503 |
| tests/e2e/prototype_shell.spec.js | ✅ passing | Shell loads clean |
| tests/e2e/golden_path.spec.js | ⚠️ targets /app | Legacy founder workbench |
| tests/e2e/prototype_jdf.spec.js | ❌ not written | Ingest + search E2E |
| tests/e2e/prototype_surgical.spec.js | ❌ not written | Atomic edit + verification E2E |

## Capability Audit Results (2026-09-17)

Nine modules formerly marked "unclear" — all LIVE, none orphaned.

| Module | Purpose | Route | Maps to client ask |
|---|---|---|---|
| fast_router | Intent classification + source detection | /api/runs | Pipeline routing |
| auto_compiler | Auto-pipeline from directive | /api/runs | Automation |
| perplexity_agent | Web search augmentation | (via runs) | External sources |
| vault_tfidf_cache | Vault ranking + cache | (via substrate) | **Knowledge vault** |
| founder_redhat | Adversarial audit | /api/runs/<id>/redhat | **Red-team** |
| full_context_scan | Z3 + Red-Hat scan | /api/projects/<id>/scan | **Verification** |
| macro_verify | Cross-run contradictions | /api/runs/<id>/contradictions | **Verification** |
| provenance_meta | Node provenance | (via audit_summary) | **Provenance** |
| verification_dossier | Certificate → PDF | /api/projects/<id>/export | **Audit export** |

Additional routes confirmed:
- /api/library/* — prompt library (classes + prompts + versions)
- /api/account/delete — user deletion (regulatory erase building block)
- /api/account/export — GDPR export
- /api/analytics/compliance-velocity — compliance tracking

**Implication:** the prototype shell surfaces ~5% of the backend.
The client's asks are mostly built. The work is exposing, not creating.

**Revised v1 estimate: 8–14 days (down from 17–29, from 36–63).**

## Known debt
1. Two JDF formats coexist: PyMuPDF (/import-pdf) vs jdf-cli (/api/projects/<id>/jdf/ingest)
2. Keyword search only — semantic embed deferred
3. No delete/forget path for ingested documents
4. No rate limiting on ingest
5. Chunk strategy hardcoded to "section"
6. systemd + docker deploy paths coexist
7. Deploy reports success without verifying app serves
8. Secrets leaked in SSM logs (compose config)
9. Cloudflare tunnel creds in git history
10. Cost/token measurement computed but not surfaced

## CRITICAL ISSUES — needs its own design session

### I1. Auth boundary — three layers, not one switch
*Measured on staging 2026-09-18T20:48Z. Note the flag is `ASSURE_` (the brief
sometimes spells it `ASSUME_`); it is **unset**, and it does not govern this.*

- **Outer — the shell access key** (`SHELL_ACCESS_KEY`, `/etc/assure/shell-access.env`):
  still required, and the only factor for `/api/health` and the programmatic API. A
  bearer token on that key carries no user identity — it returns the unrestricted
  workspace, so it is an operator credential, not a session.
- **Inner — the Clerk session**: required for the shell documents and for
  `/api/projects`; `/api/*` generally takes a session except the paths in
  `PUBLIC_API` (the sign-in flow itself, health/status probes, provider webhooks, and
  the worker-secret substrate ingest). Session required, key alone → 401 on
  `/api/projects`, 302 to `/signin` on `/`.
- **`ASSURE_ENFORCE_OWNERSHIP` is UNSET and does not govern any of this.** It was the
  old single toggle. What switches ownership on now is the presence of a Clerk
  identity: `middleware.ownership_enforced()` falls back to
  `auth_required() and current_user_id()`, admins bypass the owner comparison, and a
  NULL-owner row stays open. Setting that variable is neither required nor sufficient.

`ASSURE_CLERK_ONLY=0` in the box env restores the pre-Clerk posture (key alone opens
`/` and `/api/projects`) on the **next request — no restart**. See the demo-day
runbook's rollback section.

Still open, and the reason this section survives: the outer and inner factors are
independent, so the shared key remains a full-access credential for the programmatic
path until it is rotated or retired (deferred until after the demo).

### I2. JDF carries three kinds of sensitive input
The JDF ingested via `/api/projects/<id>/jdf/ingest` may contain:
  (a) Prompt response — user-specific AI output
  (b) Uploaded document — PII, business confidential
  (c) Both merged — combined sensitivity

Today there is no per-source isolation. Ingest from any of the three
paths lands in the same chunk index. Cross-project search is only
prevented by the ownership gate — which is currently off.

### I3. Design questions (unresolved)
- Should JDF routes bypass ownership in dev, or always require auth?
- If dev needs bypass, should it be per-route rather than global?
- Should staging enforce auth so we test what prod will do?
- Should JDF chunks carry a `source_kind` field (prompt | doc | merged)
  for provenance and future isolation?
- Should merged JDF have a different retention/erase policy than
  prompt-only or doc-only?

**Recommendation:** move to per-route auth with explicit dev-only
bypass list. Staging should enforce by default. Design session before
the next sprint.

## Next steps (planned)
1. Write prototype_jdf.spec.js — E2E ingest + search
2. Write prototype_surgical.spec.js — E2E atomic edit + verification
3. Run all four suites against staging
4. Design session: auth boundary + source isolation (I1–I3)
5. OSS integration sprint: FlowX OpenCover, gdpr-officer, LightningParse