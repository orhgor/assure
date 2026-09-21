# Live state
Last verified: 2026-09-17 · branch prototype/shell-skeleton · HEAD 33b5681

Sources: the four read-only Phase 1 investigations (A1 Pi surface, A2 OMP contract, A3 doc drift, A4 OMP isolation). Evidence classes used below: `SSM` = read-only commands on `i-03e39eccc57572191`, `HTTP` = live probes from the workstation, `CODE` = file:line in this checkout. The local checkout at HEAD `33b5681` is byte-identical to what the box serves (`prototype/shell.js` md5 `49faa5aea61bd41d0bf759d839f4a49e` locally and at `/home/ubuntu/assure-prototype/prototype/shell.js`) (A3 §0).

## What serves what

| Hostname | Serves | Port on the box | Evidence |
|---|---|---|---|
| `staging.getassureai.com` | Cloudflare Tunnel → `assure-prototype-static.service` (`prototype/dev-server.py`, `UPSTREAM_BASE=http://localhost:8890`) → `assure-prototype.service` (`python -m prompt_matrix.web --port 8890 --host 127.0.0.1`, WorkingDirectory `/home/ubuntu/assure-prototype`) | `:8891` (0.0.0.0) → `:8890` (127.0.0.1) | SSM `/etc/cloudflared/config.yml` (`staging → http://localhost:8891`); SSM `systemctl list-units` + unit contents; SSM `ss -ltnp` (A3 §0) |
| `prototype.getassureai.com` | the same chain as `staging.` (the two names are the same service) | `:8891` → `:8890` | SSM `/etc/cloudflared/config.yml` (`prototype → http://localhost:8891`), catch-all `http_status:404` (A3 §0) |
| `app.getassureai.com` | the same 14971-byte shell page as `staging.`/`prototype.` (`<title>Assure AI — Shell Prototype</title>`; `shell.js` md5 identical on all three; `/app` → 302 → `/`; `/health` → 404 from the Python dev-server) — but this hostname appears nowhere in the staging box's cloudflared ingress, and DNS is orange-clouded, so **which origin serves it is not determinable from evidence** (A3, "Not determinable from evidence") | not identifiable from the staging box | HTTP probes; A3 §0 |
| `getassureai.com` (apex) | marketing from R2 behind the `assure-marketing-proxy` Worker; `/app` → 302 → `app.getassureai.com/app` → 302 → `app.getassureai.com/`; `/health` → 200, served by the production app (`build_sha f4e2d208c0fc…`, `ui.js_version`/`css_version` `assure-127`, `jdf_workbench true`, `checks.omp ok`, `omp_version 0.1`, `disk_free_gb 21.22`, `uptime_seconds 1228744`) | none on the staging box; the `f4e2d20` app's host is not determinable | HTTP probes; A3 acceptance table (`cloudflare-fix.md` correction) and A3 "Not determinable" |
| no hostname — OMP memory server | `omp.service`, npm `omp-server` 0.2.0 ("Open Memory Protocol — reference server"), `OMP_DB_PATH=/home/ubuntu/.omp/omp.db`, DB one table `memories` + FTS5 (`memories_fts`) | `:3456` (`*`, i.e. all interfaces) | A2.7 (SSM `systemctl cat omp.service`, `ss -tlnp`); A3 §0 |

Shell behaviour measured on all three shell hosts: `/` 200, `/shell.js` 200, `/shell.css` 200, `/api/health` 200 (2181 bytes byte-identical on all three), `/api/projects/<id>/jdf/health` 200 (`jdf_bin /opt/node-v24.11.1-linux-arm64/bin/jdf`), `/api/projects/<id>/jdf/search` without `query` → 400 (A3 §0, A3.3h).

## The entry gate (added 2026-09-18)

Every one of those 200s was unauthenticated, and `/api/projects` returned all
99 projects. `prototype/dev-server.py` now requires `SHELL_ACCESS_KEY`; with it
unset the process refuses to start rather than serving an open door.

| Request | Unauthenticated | With the key |
|---|---|---|
| `/`, `/shell.js`, `/shell.css` | 302 → `/auth` | 200 |
| `/api/*` (incl. the JDF routes) | 401 | 200, proxied |

`/auth` is the only public path: it takes the key, keeps it in `localStorage`,
and a successful POST sets an HttpOnly cookie so `<link>`, `<script>` and the
streaming SSE frames carry it without JS. `shell.js` also puts the key on every
same-origin call as `X-Shell-Key`; a 401 sends the tab back to `/auth`.

- The key lives in `/etc/assure/shell-access.env` (mode `600 root:root`), pulled
  in by the drop-in `/etc/systemd/system/assure-prototype-static.service.d/access-key.conf`.
  Keep it out of the unit file and out of the repo.
- `/etc/assure/shell-access.env` is the **only** source; systemd reads it at exec
  time, so changing the key means editing that file and
  `systemctl restart assure-prototype-static.service`.
- The gate is per process, not per hostname. `app.getassureai.com` reaches this
  same `:8891` (measured 2026-09-18: its `/auth` is byte-identical to this box's),
  so the gate covers it too.

Nothing listens on `:8765`; the only listeners are `127.0.0.1:8890`, `0.0.0.0:8891`, `*:3456` (SSM `ss -ltnp`, A3 §0).

## Deploy path

What A3 established about shipping a change: the box checkout is `/home/ubuntu/assure-prototype` on branch `prototype/shell-skeleton`; the remote is `git@github.com:orhgor/assure.git`; fetch works (`git ls-remote` returns refs; remote `prototype/shell-skeleton` = `33b5681`, `staging` = `f341a65`, `main` = `965685a`, HEAD `d5d6842`); **push from the box is impossible** — `git push --dry-run origin HEAD:refs/heads/__drift_probe` → `ERROR: The key you are authenticating with has been marked as read only.` (SSM, A3 §0 / A3.2 §5); and there is **no docker on the box** (`docker: command not found` in the cleanup log; SSM), so the served path is systemd only (A3 acceptance table, `deploy-flow.md` correction).

The fetch-based path A3's evidence supports — run on `i-03e39eccc57572191`:

```
cd /home/ubuntu/assure-prototype                      # SSM git log/status (A3 §0)
git fetch origin                                      # A3.2 §5: ls-remote works, fetch path intact
git checkout prototype/shell-skeleton                 # A3 §0
git pull --ff-only origin prototype/shell-skeleton    # remote ref = 33b5681 (A3 §0)
sudo systemctl restart assure-prototype.service assure-prototype-static.service
```

The last line is **an assumption, not a Phase 1 finding**: A3 records the two units and their `ExecStart`s (`assure-prototype.service` = `prompt_matrix.web --port 8890 --host 127.0.0.1`; `assure-prototype-static.service` = `prototype/dev-server.py`, `PORT=8891`, `UPSTREAM_BASE=http://localhost:8890`) but records no deploy script or restart command. What would settle it: the unit files' full contents (`ExecReload`) and any deploy script on the box.

**Re-tested 2026-09-18 (Phase 0).** The box's deploy key is still read-only —
`git push origin HEAD:refs/heads/prototype/shell-skeleton` → `ERROR: The key you
are authenticating with has been marked as read only.`, and the same to a
throwaway ref. `git ls-remote` on the same remote works, so the box can read and
cannot write.

The path that does work: **push from the workstation to `origin`, then fetch on
the box.** `git push origin prototype/shell-skeleton` from the checkout here
succeeded (`8115964..1934165`), and the box then ran

```
git fetch origin prototype/shell-skeleton
git checkout prototype/shell-skeleton
git reset --hard origin/prototype/shell-skeleton
sudo systemctl daemon-reload && sudo systemctl restart assure-prototype-static.service
```

leaving box HEAD == origin == local at `1934165`. There is **no SSH ingress** from
the workstation to the box (TCP 22 times out), so `rsync` cannot be the transport;
the commit is the transport. `sudo` is needed on the box for `systemctl` and for
the `git config --global --add safe.directory` the checkout needs (it is owned by
`ubuntu` while SSM runs as `root`, and `HOME` is unset there — `export HOME=/root`
first).

Push-based promotion is additionally paused out of band: `.cursor/rules/deploy-flow.mdc:44` says do not `git push` to `staging`/`main` (A3, `v1.5-production-promotion.md` correction). The GHCR/docker paths in `docs/deploy-flow.md` and `docs/runbooks/v1.5-production-promotion.md` describe a mechanism that is gone from this box (A3 acceptance table).

## Retired

| What used to exist | Replaced by | Evidence |
|---|---|---|
| `/home/ubuntu/assure` | **ABSENT** — the only checkout is `/home/ubuntu/assure-prototype`; `/home/ubuntu/` now holds `assure-prototype`, `old-staging-backup`, `actions-runner`, `.omp` | SSM `test -d /home/ubuntu/assure` → `ABSENT`; `ls -la /home/ubuntu/` (A3 §0) |
| `assure.service` (Flask on `:8765`) | `assure-prototype.service` (`:8890`) behind `assure-prototype-static.service` (`:8891`) | unit file still on disk (`/etc/systemd/system/assure.service`, 881 B, Sep 10 11:13) with `WorkingDirectory=/home/ubuntu/assure`, `PORT=8765`, `ExecStart=…/assure/.venv/bin/python prompt_matrix/app.py`; `is-enabled`=disabled, `is-active`=inactive; journal `status=203/EXEC`, `restart counter is at 825`, last stop `Sep 17 18:34:53` (SSM, A3 §0) |
| port `8765` | nothing — no listener | SSM `ss -ltnp` (A3 §0) |
| auto-heal cron (`curl :8765/health` every 5 min) | nothing — no health/restart cron exists | SSM `crontab -l`: root has only two entries, both `cd /home/ubuntu/assure` (prune_logs 03:00 daily; free-disk-cleanup 04:00 Sun), both now failing at `cd`; ubuntu crontab empty (A3 §0) |
| docker / GHCR image deploy | systemd units only | no `docker` binary on the box; A3 acceptance table (`deploy-flow.md` correction) |
| workbench at `/app` on `staging.`/`prototype.`/`app.` | shell prototype at `/`; `/app` → **302** → `/` | shell commit `aedd3b4` "fix(shell): `/app*` redirects to `/` for legacy links"; measured on all three hosts (A3.3b) |
| `data/history.sqlite` on the box | legacy copies only, in `/home/ubuntu/old-staging-backup/` | `data-history.sqlite` 761856 B, `data-staging-history.sqlite` 22388736 B, `prompt_matrix-history.sqlite` 364544 B, all Sep 17 11:56 (SSM, A3 §0) |
| `sqlite3` CLI on the box | none — DB reads must use the Python `sqlite3` module against `file:…?mode=ro` | `sudo: sqlite3: command not found` (SSM, A2 box probe limits; A3 §0) |
| repo tunnel assets as the source of truth | the box's live tunnel | repo `scripts/aws/cloudflared-config*.yml` name tunnel `3c71a11e-e98b-4f11-802e-8674b8bca524`; the box's tunnel id is `fe93535b-a2cb-461d-a8ef-143f07c35876` (SSM, A3 §0) |
| docs that describe all of the above | stale — do not trust them | `docs/state.md`, `deploy-flow.md`, `post-launch-ops.md`, `product-status.md`, `webpage-all-content.md`, `runbooks/staging-launch-execution.md`, `runbooks/v1.5-production-promotion.md`, `runbooks/clerk-marketing-workbench.md`, `functionality-test-report.md`, `launch-checklist.md` (staging rows), `ship-timeline.md` (environment refs), `workbench-ui-current-state.md`, `assure-ai-all-functions.md` (staging rows), demo `README.md`/`demo-script.md`/`one-pager.md`, `cloudflare-fix.md`, `cloudflare/cache-rules-v2.md`, `queue.md` item 2. Current: `anti-claims.md`, `queue-3-4-designs.md`, `github-actions-minutes.md`, `deferred.md`, `user-experience.md`, `visual-checklist.md`, `audits/*` dated records, `runbooks/marketing-r2-free-tier.md`, `runbooks/substrate-r2-ephemeral-uploads.md` (A3 acceptance table) |

## Brand source

The shell's mark and palette are **sourced from marketing**, not owned by the shell:

- `prototype/favicon.svg` is a byte-identical copy of `prompt_matrix/static/favicon.svg` — both md5 `ecc3567873e8feb2045829160f672b64` (CODE).
- The brand block in `prototype/shell.css:32-34` cites marketing's tokens by name: `--brand-navy: #1A4B8C` = `--assure-trust-blue`, `--brand-green: #2E7D32` = `--assure-confidence-green`, `--brand-gold: #D4A843` = `--assure-accent-gold`, all defined at `prompt_matrix/static/landing.css:2-4` (CODE).

Moving one side alone is what makes the two surfaces drift: the shell would serve a stale mark, or a colour marketing no longer uses. **When marketing's brand changes, update `prototype/favicon.svg` and the brand tokens in `shell.css`'s `:root` in the same commit** — re-copy the mark rather than redrawing it. `shell.css:28-31` states the same rule at the point of use, so neither surface can be changed in ignorance of the other.

Not covered by that rule: the shell's own tokens (`--bg-*`, `--text-*`, `--border`, type scale) are the shell's and were only *aligned* to marketing's values — a marketing canvas-colour change is not a shell edit unless the shell reads that token by name.

## Constraints

- **The box deploy key is read-only.** `git push --dry-run` → "marked as read only" (SSM, A3 §0); both `/home/ubuntu/.ssh/id_ed25519` and `assure_deploy` authenticate as repo deploy keys; fetch still works. Anything that assumes a push from the box is invalid.
- **No docker on the box** (`docker: command not found`; SSM, A3 §0). Any runbook step that calls `docker compose` will fail.
- **The two surviving crontab entries are dead** — both `cd /home/ubuntu/assure`, a directory that no longer exists (SSM `crontab -l`, A3 §0). Scheduled prune and free-disk cleanup are therefore not running.
- **No `sqlite3` binary on the box**; DB reads require the Python `sqlite3` module with `mode=ro` (A2 box probe limits). `journalctl -u omp.service` returns `-- No entries --` and `/home/ubuntu/.omp/server.log` does not exist, so OMP request history is not recoverable from logs (A2 box probe limits).
- **OMP is not internet-reachable, but it is one credential for the whole instance.** `:3456` binds all interfaces; there is no cloudflared ingress for it; the security group `sg-006d2cd5bc7c3877c` permits only tcp/8891; `ufw` is inactive; an external `curl http://3.237.106.38:3456/v1/health` returned `http_000` (A2.7, A4.5). The same single bearer key authorises every route, including `GET`/`PUT`/`DELETE /v1/memories/{id}` (A4 verdict #2; `index.js:19-31`, `routes/memories.js:34-54`), so anything on the same subnet holding the key owns every row.
- **The app's OMP reads are unscoped.** `omp_recall` sends only `{q, limit, mode}` (`omp_client.py:141-146`); a foreign writer's row can be returned as the app's own data, reproduced end to end into the Red-Hat prompt (A4 verdict #1, Counter-example A). `_best_memory_content` falls back to `memories[0]` when nothing matches the key (`omp_client.py:174-181`).
- **On staging, `/api/*` is open to loopback callers.** `loopback_api_bypass()` (`cloud_auth.py:100-106`) applies whenever `require_auth` is false (`web.py:424-425`), and the box runs `ASSURE_ENV=staging` — so `GET /api/omp/recall` returns raw OMP rows to anything on the box with no credential (A4 Counter-example B). Staging also has no ownership enforcement — `ASSURE_ENFORCE_OWNERSHIP` is unset there (A3 acceptance table: `docs/queue-3-4-designs.md` is current and records the presence check over SSM).
- **Two production surfaces cannot be managed from this box.** The origin of `app.getassureai.com` and the host of the `f4e2d20` app behind apex `/health` are both not determinable (A3, "Not determinable from evidence") — settling it needs the Cloudflare zone config or SSM access to `i-09d0ad0b561113abe`.
- **OMP has no AI key on this box**, so `POST /v1/extract` and `POST /v1/compress` answer `422 no_api_key` (A2 endpoint table; `/proc/601/environ` carries only `OMP_API_KEY`, `OMP_DB_PATH`, `OMP_PORT`, A2.7).
- **The OMP database is not backed up by anything today** — no backup script references `/home/ubuntu/.omp`; neither `omp.service` nor any sibling sets `MemoryMax` (A4.5 §8).
- **OMP is reachable only from the box**, and Pi is not installed there: `which omp omp-mcp` is empty, `/home/ubuntu/.omp` holds only `api_key` + `omp.db*`, and the global node_modules holds only `@uurtech, corepack, npm, omp-server` (A1 §6, A2.7). On the workstation the same `~/.omp/` directory is **Pi's** config root *and* the Mac's local OMP server data dir — say which one you mean (A1 naming trap; A4 terminology note).
- **Phase 1 left one write behind.** An identity probe created a live project `drift-probe-1b1d91` on `app.getassureai.com`; cleanup is a single authenticated `DELETE /api/projects/drift-probe-1b1d91` and was not performed because Phase 1 forbids writes (A3, Disclosure). Leftover `/tmp` probe files also remain on the box (`/tmp/a4q.py`, `/tmp/a4q2.py`, `/tmp/ompq.db*`, `/tmp/hq.db*`, `/tmp/params*.json`; the workstation copies were deleted) (A4, leftovers).
