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
