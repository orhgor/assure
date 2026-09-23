# Decisions

One entry per decision. Every line traces to a Phase 1 finding (A1 Pi surface, A2 OMP contract, A3 doc drift, A4 OMP isolation); evidence classes: `SSM` = read-only commands on `i-03e39eccc57572191`, `HTTP` = live probes, `CODE` = file:line in this checkout. Where a report records the change but no rationale, the `Because:` line says so and names what would answer it; nothing here is invented.

## Point both `staging.` and `prototype.` at `:8891`

Chose: one ingress target for both hostnames — `staging.getassureai.com → http://localhost:8891` and `prototype.getassureai.com → http://localhost:8891`, with a catch-all `http_status:404` (`/etc/cloudflared/config.yml`, SSM, A3 §0). `:8891` is `assure-prototype-static.service` (`prototype/dev-server.py`, `PORT=8891`, `UPSTREAM_BASE=http://localhost:8890`), which fronts `assure-prototype.service` (`prompt_matrix.web --port 8890 --host 127.0.0.1`) (SSM unit contents, A3 §0).

Rejected: a distinct origin per hostname (nothing in the ingress suggests one), and the repo's own tunnel assets as the source of truth — `scripts/aws/cloudflared-config*.yml` name tunnel `3c71a11e-e98b-4f11-802e-8674b8bca524`, while the box runs tunnel `fe93535b-a2cb-461d-a8ef-143f07c35876` (SSM, A3 §0), i.e. the asset is stale.

Because: the mapping and the stale asset are recorded (A3 §0, A3.3a — the mapping is absent from every doc), but **no Phase 1 report records the intent behind the repoint**. Not determinable from evidence. What would answer it: the git history of `scripts/aws/cloudflared-config*.yml` / the tunnel change, or the Cloudflare zone's tunnel configuration.

## Retire `/home/ubuntu/assure` (and `assure.service` on `:8765`)

Chose: serve from `/home/ubuntu/assure-prototype` (branch `prototype/shell-skeleton`, HEAD `33b5681`, clean tree) via `assure-prototype.service` (`:8890`) behind `assure-prototype-static.service` (`:8891`).

Rejected: the `/home/ubuntu/assure` checkout and its unit. `/home/ubuntu/assure` is absent (`test -d` → `ABSENT`); `assure.service` still exists as a file (881 B, Sep 10 11:13) but is disabled/inactive, its journal shows `status=203/EXEC`, `restart counter is at 825`, last stop `Sep 17 18:34:53`; nothing listens on `8765` (SSM, A3 §0). Its databases survive only as `/home/ubuntu/old-staging-backup/{data-history.sqlite 761856 B, data-staging-history.sqlite 22388736 B, prompt_matrix-history.sqlite 364544 B}`, all Sep 17 11:56 (SSM).

Because: A3 establishes the end state and the leftover evidence (A3 §0, A3.2 §1, A3.3c) but **records no decision text and no rationale** for the move. Not determinable from evidence. What would answer it: the ops change/runbook that created the `assure-prototype*` units, or an ops log around Sep 17 11:56 (the timestamp on the archived databases).

## Report `skipped` instead of an unearned pass (verification honesty)

Chose: distinct non-pass states for verification that did not happen. Compile path: `redhat_status = "skipped"` with `redhat_skip = "no Red-Hat audit was requested for this compile"` (`prompt_matrix/routers/draft.py:662-663`); metric checks set `status="SKIPPED"` with `skip_reason` when there are 0 locks or no `key: value` metric (`draft.py:248`, `:255`), exposed at `:273`; the gate returns `review` unless Z3 is `PASS` with zero red-hat critiques (`prompt_matrix/services/audit_summary.py:59-66`) and is forced to `gate_status="review"` + `unverified` when `anchored == 0` (`:143`). Shell surfaces: `prototype/shell.js:812-813`, `:3484-3485`, `:2720`. Tests: `tests/e2e/test_shell_honesty.py`. Commit `2b63d02` (CODE, A3.3g).

Rejected: the prior behaviour — asserting verification the code did not perform (commit subject `fix(verify): stop asserting verification the code does not perform`, `2b63d02`, A3.3g).

Because: the behaviour and its file:line are recorded by A3.3g; A3 records no prose rationale beyond that commit subject. Why `skipped` beats a false `ran` — the reason the states are distinct is not stated in Phase 1: **[inference]** a named non-pass state is truthfully distinguishable by a reader or downstream consumer, whereas an unearned pass is indistinguishable from a real one. What would confirm it: the verify-fix commit's message body or the audit entry that produced `2b63d02`.

## Widen the OMP recall window (10 → 50 → 100)

Chose: `_RECALL_LIMIT = 100` in the JDF path (`prompt_matrix/services/jdf_memory.py:223`, used at `:233` with `limit 100`).

Rejected: the 10-slot window. It was too small for chunk retrieval — the app's own measurement, quoted in A4 Counter-example C, is that for the query "liability limit" **0 of the 10 slots were chunks** (`jdf_memory.py:213-222`). The cache and Red-Hat paths still use the untouched client default of 10 (`omp_recall(key, limit=10)`, `omp_client.py:134`; `safe_omp_recall` passes no limit, `omp_client.py:201-206`).

Because: the chain is recorded as 10 → 50 (commit `5c578ef`) → 100 (commit `3bb2bed`), with the stated reason in the code that "OMP caps `limit` at 100" (`jdf_memory.py:217-222`, A3.3f). Server side confirms the ceiling: `limit` is `1..100, default 10` (`omp-server` `dist/types.js` `SearchMemoriesSchema`; `types.ts:31`), and a live `POST /v1/memories/search` with `limit 200` returned **422** (A2.5, A4 corrections).

Correction to the brief: this was not a 10 → 50 change. 50 was an intermediate value; the current value is 100 (A3 contradiction #2, A3.3f). Both intermediate and final values are recorded above.

## Shell constant floor (8 → 4)

Chose: **not determinable from evidence.** No Phase 1 finding reports a shell constant change from 8 to 4: a case-insensitive search of all four reports for `floor`/`FLOOR` returns zero matches, and none of the commits A3 cites (`2b63d02`, `3bb2bed`, `aedd3b4`, `5c578ef`, `de6e827`) is described as a floor change (A1–A4 as a set).

Rejected: not determinable from evidence.

Because: not determinable from evidence. A3.3g covers the neighbouring verification-honesty work — the gate is forced to `review` + `unverified` when `anchored == 0` (`prompt_matrix/services/audit_summary.py:143`) — but names only the shell surfaces `:812-813`, `:3484-3485` and `:2720`, and no Phase 1 finding mentions any floor constant or an 8 → 4 change. What would answer it: the commit that changed the shell's anchor floor constant and the file that holds it (`git log -S` over the shell source), or the audit entry that prompted the change.

## Redirect legacy `/app*` links to `/`

Chose: `/app` and `/app*` on the shell hosts redirect (302) to `/` (commit `aedd3b4` "fix(shell): /app* redirects to / for legacy links"; measured on `staging.`, `prototype.` and `app.`, A3.3b). Apex `/app` → 302 → `app.getassureai.com/app` → 302 → `app.getassureai.com/` (HTTP, A3 §0).

Rejected: continuing to serve the founder workbench at `/app` on those hosts — every doc that assumes it is now wrong (`docs/product-status.md:15`, `launch-checklist.md:8`, `functionality-test-report.md:4`, `runbooks/staging-launch-execution.md:81-87`, `runbooks/clerk-marketing-workbench.md:36-96`, demo `README.md:14`, A3 acceptance table).

Because: the commit subject states the intent ("for legacy links") and the behaviour is measured; A3 records no further rationale. Whether the workbench is retired permanently or moved is **not determinable from evidence** — settling it needs the product decision, not more probes.

## Do not put a second writer on the app's OMP instance as it stands

Chose: keep the app's OMP instance (`omp.service`, `:3456`, single writer `source_tool='assure'`, one namespace `project:prompt-matrix`, 1160 rows, A2.2/A2.7/A4.2) single-writer until all three conditions in A4's verdict are met: (1) the app scopes every recall by namespace (`omp_client.py:141-146`) and the second writer uses its own namespace; (2) the first-row fallback in `_best_memory_content` (`omp_client.py:172-181`) is removed or guarded; (3) the second writer has its own credential — impossible in `omp-server` 0.2.0, which has one bearer key for the whole instance (`index.js:19-31`), so (3) means a second instance.

Rejected: sharing one instance and one key. Reproduced failures: the app's Red-Hat context was served a foreign writer's text end to end (A4 Counter-example A); `omp_delete_memory(<foreign id>)` returned `True` (Counter-example D); a `namespace` is a filter the reader may choose to apply, not an access boundary (`sqlite.js:164`), and the app's read path never applies it (A4 verdict #3, A4.4). `search.tags` is accepted by the schema and ignored by the storage layer (`types.js:27` vs `sqlite.js:159-181`), so tags cannot scope a read either.

Because: A4's verdict, reproduced on a throwaway instance, plus the pricing of the alternative in A4.5 (a second `omp.service` on `:3457` with its own DB dir and key: ~70–80 MB RAM against a box with 1016 MB available, a few MB of disk against 33 GB free, one new systemd unit, no security-group change, and a second store that nothing currently backs up). Note the app cannot be moved between instances by environment alone — the key path `~/.omp/api_key` is hard-coded (`omp_client.py:20`) and `OMP_API_KEY` is only a `FileNotFoundError` fallback (`:48-51`) (A4.5 §9).

## Production promotion — deliberately not taken (2026-09-19)

Chose: the wave stays on `prototype/shell-skeleton` and the demo is served from the box at its current HEAD. `main` is not touched and nothing is pushed (CODE: HEAD `19e37fa`/`b315620`/`83204d0`; SSM: `staging.getassureai.com` → `:8891` → `:8890`).

Rejected: promoting to `main`. Three prerequisites the owner named, in this order:

- **a live Clerk instance** (`pk_live_`/`sk_live_`). The credentials in play are **test-mode, a dev instance** (`pk_test_`/`sk_test_`, measured 2026-09-18T20:48Z; SSM/HTTP), and Phase A makes the shell require a session — promoting as-is would gate production with a dev instance.
- **a decision about what production serves** — the shell, the workbench, or both; `main` and staging currently run a different lineage (`assure-127`/`assure-140` via Docker + GHCR) from this prototype branch.
- **the `docs/demo/` removal ported to `main` first** — the fixtures were moved off the prototype branch only, so `main` and `staging` still carry them.

Because: recorded so the next person finds the reasoning rather than rediscovering it. GitHub remains paused (2026-09-07); nothing in this entry authorises a push.

Still open, and the one that could bite a demo: reaching the demo now requires a Clerk sign-in, **and sign-in currently requires an email-code second factor** (HTTP: `needs_second_factor` with `supportedSecondFactors: [{strategy: email_code}]`, measured 2026-09-18). A presenter without a readable mailbox therefore needs the `ASSURE_CLERK_ONLY=0` flip — one line in `/home/ubuntu/assure-prototype/.env.staging`, no restart (HTTP: process ids `1271572`/`1271580` unchanged across all three states). The pre-flight section of `docs/runbooks/demo-day-staging.md` carries both paths, so the second factor is stated up front rather than arriving as a surprise.

## Two auth-gate defects — fixed in the tree, `PENDING DEPLOY` until after the demo (2026-09-19)

**Status: `PENDING DEPLOY`.** Both fixes are committed on `prototype/shell-skeleton` (`ec3b30d`, `bd5e998`) and
are **not** on the box. Nothing was written, restarted, or committed on `i-03e39eccc57572191` while preparing
them; its HEAD is still `daafc0b` and both units are active (`assure-prototype`, `assure-prototype-static`).

Chose: repair both defects in the local tree now and land them in one explicit step after the demo, rather
than change a live auth gate in the window before it.

- **D1 — the gate fails open when it cannot read its own state.** `clerk_only_mode()` (`prototype/dev-server.py:76-87`)
  wrapped its read of `/api/auth/config` in `try/except` and returned `False` on any exception, so an app that
  could not be reached made the document gate Clerk-**optional** and the shell rendered for a visitor with no
  session — the state the flag exists to prevent. Fix (`prototype/dev-server.py:76-98`): the config read is the
  only thing that reports that state, so an unreachable app — and a reply that does not carry the flag — take
  the conservative branch, Clerk-**required**. Verified locally against a stub app and real gate instances:
  unreachable config → `clerk_only_mode()` is `True` and `GET /` (with the key, no session) is **302 `/signin`**
  where the pre-fix build served **200**; reachable config with the flag `1` → **302 `/signin`**; the
  `ASSURE_CLERK_ONLY=0` rollback still serves the shell (**200**); assets stay ungated (`/shell.js` → 200).
- **D2 — every same-origin 401 bounced to the key page.** `bounceOnUnauthorized` (`prototype/shell.js:32-41`)
  sent any 401 to `/auth`, so D1's fail-open produced a loop: the shell rendered with no session, its first API
  call 401'd, the reader was pushed to the key page, entering the key loaded the shell again, and the same 401
  fired again — Clerk never appeared. Fix (`prototype/shell.js:45-124`): the 401 names its cause before anyone is
  moved. The gate's own denial is recognised by its marker (`WWW-Authenticate: Bearer realm="assure-shell"`,
  `dev-server.py:_deny`) → `/auth`; an app 401 is answered by asking the public `/api/auth/me`
  (`cloud_auth.py:PUBLIC_API` — it answers 200 with an empty `user_id`, not 401) → empty `user_id` means no
  session → `/signin`; a live `user_id` while another request 401s means the refusal is not about the session →
  the error is surfaced and the page is left alone; a non-401 (500) passes through untouched. Verified locally in
  Chromium against the real gate and `shell.js`: expired session → **`/signin`**; key cookie removed → the key page
  re-prompts at **`/auth`** and one key entry returns to a working shell (no loop); D1's fail-open on the **pre-fix**
  shell still loops (`401 → /auth → key → / → 401 → /auth`), which is the reproduction; 401 with a live session →
  error shown, page kept; 500 → passed through, no bounce; normal signed-in operation unchanged.

Rejected: correcting either defect on the box before the demo. `prototype/shell.js` and
`prototype/dev-server.py` are served from disk with `Cache-Control: no-store` (`assure-prototype-static` runs
`python prototype/dev-server.py` from `/home/ubuntu/assure-prototype`), so writing them into the box's tree is
live on the next page load — **a commit there is a deploy for those files**, and a partially-landed auth change
is exactly the ambiguity this work has spent effort removing.

Because: a gate that starts requiring a session is the one change that can lock a presenter out of their own
demo. The rollback is real but narrow — `ASSURE_CLERK_ONLY=0`, one line in
`/home/ubuntu/assure-prototype/.env.staging`, read per request (`cloud_auth.py:236-243`), no restart — and it is
the presenter's escape hatch, not a reason to test that change on the live box in the demo window. Preparing both
fixes now and deploying them as one step leaves the demo's served bytes untouched and the corrections ready the
moment the demo is over.

Not verified, and deliberately so: the loop is reproduced locally against the pre-fix shell with a stub app and a
stand-in sign-in page; the box's real Clerk handshake (two-step, email-code second factor) is the acceptance step
for the deploy, not something a local run can claim.

## 2026-09-22 — PostgreSQL only, parse on workers, S3 for bytes

- **SQLite removed.** `DATABASE_URL` (PostgreSQL) is mandatory for every
  process; `db/pg_compat.py` runs the existing SQLite-dialect SQL unchanged.
  Reason: a single-writer file on one disk is the one thing that cannot be
  replicated. Legacy data: `scripts/migrate_sqlite_to_postgres.py`.
- **Uploads are queued, never parsed in a request.** `PARSE_ASYNC=1` →
  object store + Celery `parse` queue + `GET /api/tasks/<id>`. Reason: OCR is
  ~3 s/page; the request path must stay under load-balancer timeouts.
- **Scans go to jdf-cli's tesseract first, Textract second.** Reason: same
  pipeline, per-line confidence, no per-page fee. `PARSER_SCAN_BACKEND`
  overrides.
- **Redis for shared counters/locks/results, SQS (AWS) or Redis (local) as
  broker.** Reason: rate limits and debounce must be one bucket across
  replicas; SQS gives zero idle cost and an autoscaling metric.
- **Cloudflare R2 edge worker retired in favour of presigned S3 uploads.**
  See docs/scale_architecture.md §6.
- **Async verification (>50 pages) stays deferred** with a written contract
  in docs/deferred.md ("Async verification").
