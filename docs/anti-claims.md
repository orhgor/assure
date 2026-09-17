# Anti-Claims

```
| Claim | Why it's false/unverified | Correct phrasing |
|-------|--------------------------|------------------|
| "OMP provides semantic search" | v0.2.0 is keyword-only; omp_recall sends mode:"keyword" (omp_client.py:142) | "OMP provides keyword search for v1; semantic embed deferred." |
| "Path A surgical edit passes" | structurally true, outcome broken — live model returns DeepSeek AuthenticationError and the node renders an error string (runtime E2E, pathA2.log) | "The Path A rephrase flow opens/submits/closes, but model output is currently a DeepSeek auth error, so content is unverified." |
| "Free-model stack is stable" | DeepSeek auth is currently failing (runtime E2E) | "The free-model stack is unstable until DeepSeek auth resolves." |
| "Deploy reports success = app serves" | silent-success bug — a deploy was green while /workbench still 404 (image pulled but not started); state.md Known debt #7 | "Deploy success means the run finished; it does not verify the new code is actually serving." |
| "Two JDF formats are compatible" | different schemas: PyMuPDF → jdf_documents via save_jdf_revision vs jdf-cli → jdf_cli_documents ({$jdf,meta,pages}); state.md debt #1 | "Two JDF schemas coexist and are not interchangeable." |
| "Regulatory erasure is implemented" | only user-level /api/account/delete (web.py:900); no doc-level delete/forget exists (grep of routers/services empty) | "User-level delete exists; document-level erasure is not implemented." |
| "Auth is enforced on staging" | ASSURE_ENFORCE_OWNERSHIP is off; /api/projects/<id>/* is effectively public on staging (state.md I1) | "Staging currently bypasses ownership enforcement; JDF/auth routes are public until enforcement is on." |
| "Verification is surfaced end-to-end for the client" | full_context_scan / macro_verify are backend-only; no refs in prototype/shell.js (grep empty) | "Verification engines exist in the backend but are not surfaced in the prototype." |
| "The knowledge vault works from the prototype" | vault_tfidf_cache / fast_router are not wired into the prototype UI; the prototype exposes only keyword JDF search | "Vault ranking exists in the backend; the prototype surfaces only keyword JDF search." |
| "All E2E suites are green" | prototype_jdf passes; prototype_surgical_from_search FAILS (SEARCH→EDIT not implemented); golden_path FAILS (targets /app) | "Shell + JDF ingest/search pass; Path B and golden_path fail." |
```
