# V1 Definition

```
V1 one-sentence: v1 is the Assure prototype workbench on prototype.getassureai.com that lets an operator compile a draft
from an intent, inline-rephrase any node, ingest a PDF into the JDF/OMP memory vault, and keyword-search that vault —
running end-to-end on staging and verified by E2E.
V1 audience: an evaluating client/analyst using the prototype workbench on prototype.getassureai.com to check
deterministic drafting, surgical edits, and PDF memory recall.
V1 success test: all prototype E2E suites pass against staging (shell, JDF ingest+search, surgical edit with real model
output, no console errors), /api/projects/<id>/jdf/health returns 200, a live compile renders JDF nodes, a rephrase
updates node content, and a search returns the ingested fixture chunk.

V1 IS (in scope) — max 6, each with done-criterion:
  1. Shell loads clean — done when prototype_shell passes with 0 console errors and 0 failed requests.
  2. JDF ingest + keyword search — done when a fixture PDF posts to /api/projects/<id>/jdf/ingest and returns
     chunks_stored>0, and searching its unique term returns that chunk (prototype_jdf PASS).
  3. Inline rephrase of a compiled node (Path A) — done when a rendered .jdf-node opens the editor, submit closes it,
     and the node re-renders with real model output (not an error). [NOT yet: model currently returns a DeepSeek auth error]
  4. Verification surface renders after compile — done when a compile shows Red-Hat / Evidence / an anchored badge.
  5. Project-scoped auth on JDF routes — done when staging enforces ownership (unauthenticated → 401/403).
     [NOT yet: ASSURE_ENFORCE_OWNERSHIP is off]
  6. Prototype E2E suites defined & runnable — done when prototype_shell, prototype_jdf, prototype_surgical, and
     prototype_surgical_from_search all run against staging with defined expected outcomes.

V1 IS NOT (out of scope):
  - Semantic/embedding search (keyword-only today)
  - JDF search→edit (search results load into an editable node) — Path B
  - Cross-run macro-verification / full-context scan surfaced in the prototype UI (backend-only)
  - Multi-tenant source isolation, source_kind tagging, or retention/erase policies
  - Doc-level delete/forget (regulatory erasure per document)
  - Production Docker deployment as the serving path (staging uses the systemd prototype path)
  - Rate limiting on ingest

Client sign-off gate:
  On prototype.getassureai.com the client must: run a live compile that renders a real draft; inline-rephrase a node
  and see corrected content; ingest a PDF and search to find the exact chunk; and see the verification pane — with zero
  console errors — and approve the auth-boundary behavior on staging.

Unresolved (blocks v1 definition):
  - Which provider/key should the free-model stack use? DeepSeek calls currently return AuthenticationError.
  - Is auth required on staging for sign-off (enforce ownership), or is dev-bypass acceptable?
  - Does "surgical revision" require search→edit (Path B), or is compiled-draft edit (Path A) sufficient?
  - Do JDF chunks need source_kind isolation and a retention/erase policy now or after v1?
```
