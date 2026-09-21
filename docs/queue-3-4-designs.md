# Queue #3 / #4 — Designs (auth boundary, source isolation)

Both items were specified as design sessions, not unilateral changes. Facts below are from the
2026-09-17 audit (`docs/audits/2026-09-17-jdf-surface.md`) and from live verification on
`i-03e39eccc57572191` (the staging box).

---

## 1. Auth boundary (queue #3)

### What is actually true today

- All 10 JDF routes carry `@project_ownership_required`. None is undecorated.
- Enforcement is gated by `ownership_enforced()` (`prompt_matrix/middleware.py:28-37`):
  `ASSURE_ENFORCE_OWNERSHIP` truthy → on, falsy → off, **unset** → `auth_required() and current_user_id()`.
- On staging, none of `ASSURE_ENFORCE_OWNERSHIP`, `ASSURE_EDITION`, `PEM_EDITION`, `CLERK_*` is set
  (presence-checked over SSM; values never read). So `ownership_enforced()` is **False** and
  `/api/projects/<id>/*` is effectively public there.
- **The hole that makes “enforced” ≠ “secure”**: `check_project_ownership` returns `None` (allow)
  when `project_owner_id(project_id) is None` (`middleware.py:60-61`). Owner-less projects
  (`default`, every `shell-proto-*`) therefore stay open even with the flag ON.
- The prototype shell has **no Clerk session**, and its proxy (`prototype/dev-server.py`) forwards only
  `Content-Type, Accept, Authorization, Origin, Cache-Control, X-Requested-With` — `Cookie` is dropped.
  With enforcement ON, the shell can never write to any project.

### Correction to the ticket

The `{"error":"Unauthorized.","ok":false}` the queue attributed to `POST /jdf/ingest` does **not** come
from that route. It has exactly one producer — `prompt_matrix/routers/substrate.py:224`
(`/api/substrate`, the edge Worker’s ingest gated on `SUBSTRATE_INGEST_SECRET`). `/jdf/ingest` answers
`400 {"error":"no file"}` unauthenticated on the box. Already fixed by repointing the shell’s vault
upload to the ownership-gated `POST /api/projects/<id>/substrate/upload`.

### Options

| Option | Shape | Cost | Verdict |
|---|---|---|---|
| (a) Per-route policy | annotate each route `gate="ownership" \| "worker_secret" \| "none"`; the `before_request` guard (`middleware.py:100-107`) reads the policy | mechanical, touches every route + the guard; explicit and testable | **recommended** |
| (b) Global flag, enforce-by-default on staging | one env line (`ASSURE_ENFORCE_OWNERSHIP=1`) | smallest diff, but global: it breaks the shell outright (no session, cookies stripped) and owner-less projects stay open, so “enforced” still leaks | only after (a) + the hole below |
| (c) Loopback dev bypass | allow when `remote_addr` is loopback (`cloud_auth.loopback_api_bypass()`) | zero dev friction | **unsafe here** — behind the Cloudflare tunnel *every* external request arrives as `127.0.0.1` |

### Recommended sequence

1. Close the owner-`None` hole first: deny owner-less projects to non-owner callers, or auto-claim a
   project on its first authenticated write. Without this, flipping any flag buys little.
2. Adopt (a) so each route states its gate; keep `/api/substrate` on the machine secret (it is
   CSRF-exempt and the only unauthenticated write into the vault).
3. Only then default enforcement ON for staging, and give the shell a service identity (or keep the
   shell’s writes routed through an ownership-gated route with a real session).

**Open:** which surface is the product boundary (shell vs `/app` workbench) and whether the shell should
hold a service identity rather than a user session.

---

## 2. Source isolation (queue #4)

### Problem

Chunks carry no `source_kind`, so prompt text, uploaded documents, and merged output are
indistinguishable in the OMP index.

### Design

Add `source_kind: prompt | document | merged`, set at write time by the producer that owns the bytes:

| Value | Set by |
|---|---|
| `prompt` | prompt-only compiles (runs/compile path) |
| `document` | substrate + JDF ingest (`services/jdf_memory.remember_jdf_document`, `services/omp_memory.remember_vault_file`) |
| `merged` | the merge/commit path, when a compiled body incorporates both |

Surface it by (i) keeping it in the chunk payload (the payload already carries arbitrary scalars via
`meta`), (ii) adding an optional `source_kind` filter to `search_jdf_chunks` (default `None` = all), and
(iii) echoing it in search results so the UI can label provenance.

**Migration:** existing memories have no `source_kind`. Treat missing as `legacy`: returned by default,
never matched by an explicit filter. No destructive reindex required.

### Two correctness gaps in the same area (one already fixed)

- **Fixed 2026-09-17:** `get_doc_chunks` filtered on `doc_id` only, so a re-ingest returned stale chunks
  alongside fresh ones. It now resolves the durable row’s `doc_hash` and returns only the current
  generation. (QA verified end-to-end with the real derivation, not fabricated hashes.)
- **Fixed 2026-09-17:** partial OMP writes were silent (`0 < stored < total` returned HTTP 200 with a
  short `chunks_stored`). The response now carries `chunks_failed` / `partial` and logs a warning; the
  existing 503-on-total-failure contract is unchanged.
- **Open:** `_doc_hash` is not content-stable — it hashes `repr(sorted(jdf_dict.items()))` where jdf-cli
  stamps `meta.title` with the **temp filename**, so byte-identical content yields a different hash each
  ingest. The read filter works today only because the same hash is written to both the durable row and
  every chunk payload. A reproducible hash (hash `pages`, or pass the real filename into `pdf_to_jdf`
  instead of the temp stem) is the real fix and would let the filter collapse generations.

---

## Left over from queue #5, scoped separately

- `redeploy-app.sh:243-249` skips its own health/exec checks whenever `ASSURE_SSM_BACKGROUND=1` — the
  exact call shape used by the SSM path. The postflight gate in `ssm-redeploy-and-wait.sh` compensates,
  but the underlying skip is a standing footgun.
- `rollback.sh` is unreachable from the SSM path: a failed postflight leaves the bad container in place
  instead of rolling back.
- Staging `build_sha`: `staging.getassureai.com/health` returned `None`, which degraded the new gate to a
  200-only check. Fixed 2026-09-17 by falling back to the running checkout’s commit in
  `routers/health.py` when `ASSURE_BUILD_SHA` is unset (the systemd staging app never runs the docker
  path that exports it).
