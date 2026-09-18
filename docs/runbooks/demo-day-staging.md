# Demo-day runbook — the shell on staging

**Scope:** running the Boston RE insurance demo on the deployed shell prototype.
**Deployed revision:** `1cde23b` (`prototype/shell-skeleton`) on `i-03e39eccc57572191`.
**Verified:** 2026-09-18, against `https://staging.getassureai.com` (the box checkout is
`/home/ubuntu/assure-prototype`; `prototype/shell.js|shell.css|index.html` are byte-identical
to the committed revision — md5 `ccb0f4e9…`, `cb8e3e88…`, `4fff7756…`, measured
2026-09-18T20:13Z after the hygiene pass recorded in §3a). The fixture material this runbook
used to read out of `docs/demo/` now lives on the `test-fixtures` branch (§4).

---

## ⚠️ DEMO LOCKED? ONE LINE, NO RESTART

**DEMO-DAY SIGN-IN — TWO STEPS**

```
Step 1 — the gate key.
  Open https://app.getassureai.com
  The page reads: "This build is not public. Enter the access key to continue."
  Paste SHELL_ACCESS_KEY (from /etc/assure/shell-access.env).

Step 2 — the Clerk session.
  The Clerk sign-in appears. Enter the demo account email and password.
  If a code is requested, enter it from demo@getassureai.com.

If either step fails:
  Set ASSURE_CLERK_ONLY=0 in /home/ubuntu/assure-prototype/.env.staging. No restart.
  The gate key alone opens the shell. Restore: ASSURE_CLERK_ONLY=1.

Two different failures, two different responses. A code that EXPIRED is fixed by
  requesting another one — re-enter the password, or press Resend — and both are
  normal parts of the flow. Only a code that never ARRIVES reaches for the flag:
  retry first, flip on non-arrival.
```

**SIGN IN AS `demo@getassureai.com` — it owns the demo project.** The presenter reaches the frozen
`v45` document on the normal path: **no admin role and no flag.** Measured 2026-09-18 after the one-row
change below: as `demo@`, `GET /api/projects` returns 10 rows **including `demo-3235f5`**
(`{"id":"demo-3235f5","title":"workspace","current_version":45,"node_count":7,"source_count":1,"status":"ready_to_export"}`),
`GET /api/projects/demo-3235f5/jdf` → **200** (7 paragraphs, 5 anchored), the export → **200**
(sidecar 56,938 B; bundle 9,293 B), and the **warm** compile replays from cache — `omp_cached: true`,
`cache_key ast:demo-3235f5:1e9c9516`, `provenance_stats eligible 3 / anchored 3 / supported 2 /
partial 1` — leaving `current_version` at **45**. A cold compile is still refused by the freeze guard,
so the **warm path is the demo path**; never send `force=true`.

**What changed — `owner_id`, not a role.** `demo-3235f5.owner_id` was the sentinel `'legacy'` (the
backfill artifact of the pre-auth rows), which the underwriter filter excludes
(`project_routes.py:139-147`: an underwriter sees `owner_id = me OR owner_id IS NULL OR owner_id = ''`).
It is now the demo account's Clerk id `user_3JWBcRJL5Dsf51437Jfm76yhyfO`. **The account stays an
underwriter** — no role was granted — so isolation still behaves honestly and can still be shown:
re-measured after the change, a **non-owner** underwriter (`auth1d.b@`) still does not see
`demo-3235f5` in `GET /api/projects` and still gets **403** on its `/`, `/jdf`, `/substrate` and
`/export`. The `'legacy'` sentinel is now gone from the one project that matters, so the demo no
longer leans on a backfill artifact.
**Backup taken first:** `/home/ubuntu/backups/history-preflight-20260918T213722Z.sqlite`, written with
the SQLite online backup API (**not `cp`**), `PRAGMA integrity_check` → `ok`, and read back to confirm
it held `owner_id='legacy'` before the UPDATE ran. The UPDATE matched exactly 1 row; a before/after
census showed **exactly one `projects` row differing, only in `owner_id`**, with every other table
content-identical (`jdf_revisions` 317, `jdf_documents` 82) and `current_version` 45 → 45.

**If the code does not arrive at all, the fallback below still stands** — `ASSURE_CLERK_ONLY=0` in
`/home/ubuntu/assure-prototype/.env.staging`, no restart. Measured on that path: the key-only operator
has no Clerk identity, so the visibility filter is skipped entirely (`project_routes.py:141`) and
`GET /api/projects` returns all 138 rows including `demo-3235f5`, with its `/jdf` → **200**.

**The two steps are two doors, checked in this order** (`prototype/dev-server.py:362`,
`_route`): `/auth` carries the key and nothing else and is handled first (`:46 AUTH_PATH`,
`:189 _authorized` → `:192 _deny`); the Clerk session is checked only on the document path
after that (`:392` `clerk_only_mode() and is_document_path(...)` → `_redirect_to_signin()`).
Measured 2026-09-18T21:25Z with no cookie: `GET /` → `302 location: /auth`; `GET /auth` →
`200` carrying `This build is not public. Enter the access key to continue.`
(`dev-server.py:137`).

**File — this one, and not the other.** The flag is
`ASSURE_CLERK_ONLY` in **`/home/ubuntu/assure-prototype/.env.staging`** and nowhere else.
`_flag_value("ASSURE_CLERK_ONLY")` resolves the repo root and reads `.env`, `.env.local`,
then `.env.<ASSURE_ENV>` — later files winning — through `_env_file_values`, which re-reads the
file whenever its **mtime** changes (`prompt_matrix/cloud_auth.py:211`, root at `:221`, profile
from `ASSURE_ENV` at `:222`, file list at `:223`/`:225`, read at `:228`, called by
`clerk_only_enabled()` at `:236`; mtime cache at `:184`/`:187`). `ASSURE_ENV=staging` for the app
(`assure-prototype.service:11`), and `.env`/`.env.local` do not exist on the box, so
`.env.staging` is the only file consulted. Proved by executing the app's own resolver on the
box: `files consulted, in order (later wins): ['.env', '.env.local', '.env.staging']` →
`.env.staging exists=True ASSURE_CLERK_ONLY='1'` → `clerk_only_enabled() = True`.

**`/etc/assure/shell-access.env` is NOT this file — do not "correct" this section toward it.**
That file carries `SHELL_ACCESS_KEY` and **no** `ASSURE_CLERK_ONLY` (variable names read off the
box; values not printed). It is loaded as an **`EnvironmentFile=`** by
`assure-prototype-static.service:21`, so a value written there is read **at boot for that unit
only** — and the whole point of `_flag_value()` reading the env *file* with an mtime cache is to
put the switch **per request**, because a restart mid-presentation is the exact failure this
line exists to prevent. Editing it there would convert a working one-line rollback into a
service restart at the client table. The edge needs no edit of its own either: it does not read
its own environment for this — `clerk_only_mode()` asks the app's `/api/auth/config` on every
request, with no cache (`prototype/dev-server.py:76`, `:85`).

**Effect:** Clerk stops gating the shell immediately. The next request gets today's behaviour back — the shared access key alone reopens `/` and `/api/projects`. **No restart.** Measured 2026-09-18T21:24Z by flipping that one line across all three states with the process ids unchanged — api `1271801` / edge `1271580`, both `ps -o lstart` unchanged throughout: `ASSURE_CLERK_ONLY=1` → `/api/auth/config {"clerk_only":true}`, `/` **302** `/signin`, `/api/projects` **401**; `=0` → `{"clerk_only":false}`, `/` **200**, `/api/projects` **200**; `=1` again → `{"clerk_only":true}`, `/` **302**, `/api/projects` **401**.

**Pre-flight, know this before you present:** the demo now requires a **Clerk sign-in**. The gate key alone gives `/` → **302 `/signin`** and `/api/projects` → **401**; only `/api/health` still answers on the key alone (monitoring). So it is **a session, or the flag** — there is no third way in.

**Reach for the flag when:** the sign-in asks for an **email code that never arrives** (the instance requires it as a second factor and a presenter without a reachable mailbox cannot pass it), or any other moment where a session cannot be completed. Flip `ASSURE_CLERK_ONLY=0` and carry on — no restart, no bounce.

**Order of retreat — always this order:**

1. Flip `ASSURE_CLERK_ONLY=0`. Immediate, no bounce. **This is the first move.**
2. Only if the flag itself is broken: revert **A and B together** — `git revert 19e37fa b315620`. **Reverting A alone does not work**: B's assertion is what makes A's one-line removal of `/` safe, so undoing A re-adds the clash and the API refuses to start. Then `sudo -n systemctl reset-failed assure-prototype.service && sudo -n systemctl restart assure-prototype.service assure-prototype-static.service`.

**Never restart first.** A restart mid-presentation is the exact failure the flag exists to prevent.

---

**Production URL: `app.getassureai.com`.** The instance is the same box as
`prototype.getassureai.com` — one EC2, bearer-key auth, SQLite. It presents as production for
evaluation; the underlying deployment is pre-production. If asked about HA, backups, or SSO:
"Production deployment is the next phase; this instance is for evaluation."

**Cutover, 2026-09-18 (what changed under this URL).** `app.getassureai.com` and
`prototype.getassureai.com` are now the same tunnel (`assure-staging`,
`fe93535b-a2cb-461d-a8ef-143f07c35876`) and the same origin — `http://localhost:8891` on
`i-03e39eccc57572191`. The `app` CNAME used to point at the *other* tunnel (`assure-prod`),
which reached this box over the VPC (`172.31.8.21:8891`); that hop is gone. All three hostnames
(`app.`, `prototype.`, `staging.`) serve the same bytes: authenticated `GET /` returned 200 and
md5 `5a69c864134c95e125d46c156e58330c` from each, matching the box checkout. The shell's
`<title>` is `Assure` (was `Assure AI — Shell Prototype`) and a shell-created project is titled
`workspace` (was `Untitled`, before that `shell-proto`); the `prototype/index.html` md5 in the
front matter above is superseded by the value just quoted. Certificates need no action: the zone's universal cert
already covers `*.getassureai.com` (Google Trust Services WE1, 2026-09-01 → 2026-11-30).

---

## 1. What runs where — the three layers

**OMP is the memory layer.** `omp.service` (npm `omp-server`, `:3456`, DB
`/home/ubuntu/.omp/omp.db` — one `memories` table plus an FTS5 index) stores the **source
substrate**: every vault upload is written to it as a `vault`-tagged memory carrying
`filename [file_id] project=<project_id>` and the extracted text
(`routers/substrate.py` → `services/omp_memory.py:remember_vault_file`). It serves recall for
those memories, it persists across sessions (the DB is the store, not the browser), and it is
portable and self-hostable — one reference server process and one SQLite file, no managed
service. **It does not select models.** Recall is **keyword search (FTS5)** today: the client
sends `mode: "keyword"` (`omp_client.py:141-146`); semantic embedding is deferred
(`docs/anti-claims.md`). Say "keyword search", not "semantic search".

**The Assure surface is the shell** — `assure-prototype-static.service`
(`prototype/dev-server.py`, `:8891`) in front of `assure-prototype.service`
(`python -m prompt_matrix.web --port 8890`). This is where the intent is written, the compiled
prompt and the routed model are shown, the document is edited, versions are navigated, and the
Red-Hat audit is run.

**The model layer is swappable, and `cost_governance.py` chooses it.** The compile route's
model is not an env lookup at the call site: it is
`TASK_POLICIES[TaskType.DRAFT_COMPILE]` in `prompt_matrix/cost_governance.py`, read through
`routers/draft.py:82-88` (`_draft_route_model`), so the model that is called, the compile-cache
key and the ROUTED TO panel cannot disagree. Swapping the model for the demo is a change to
that policy — not a UI change, not a code change in the shell.

No Pi in this stack. Pi is not installed on the box (`which omp` is empty; the only OMP
artefact is `/home/ubuntu/.omp/` holding the server DB and key).

---

## 2. Pre-flight (2 minutes, before the audience)

| Check | Command | Expected |
|---|---|---|
| Services | `systemctl is-active assure-prototype.service assure-prototype-static.service omp.service` | `active` ×3 |
| App health | `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8890/api/health` | `200` |
| Shell health | `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8891/` | `302` — the entry gate's redirect to `/auth`; the shell itself is `200` with `-H "X-Shell-Key: $SHELL_ACCESS_KEY"` |
| Public shell | `curl -s -o /dev/null -w '%{http_code}' -L https://staging.getassureai.com/` | `200` |
| Build identity | `md5sum prototype/shell.js` in the box checkout | `ccb0f4e95c216f17865d3e2ea275dfb0` (2026-09-18T20:13Z) |
| Browser | 1440×900 or larger, **zoom 100 %** | see §7 |

All six commands above were run again after the 2026-09-18 hygiene pass (§3a) and returned
exactly those values.

---

## 3. The demo project

**Project id: `demo-3235f5`** (stored title `workspace` since the 2026-09-18T20:12Z rename recorded
in §3a; the id keeps its internal name). It is the project the shell opens on the
demo machine (`localStorage.assure_project_id`), and it is the one the probes and the H6 run used.

**It is DB-preserved: do not re-seed it for demo day.** The 2026-09-18 hygiene pass moved the
fixture files off this branch and left every row where it was — the frozen document (§10.1,
`v45`, 7 paragraphs, 5 anchored) and all 45 `jdf_revisions` included (§3b). Seeding makes a new
id, and the frozen numbers stay with the old one, so it is the **last resort** — never a
pre-flight step, and never done live (§3c: a cold compile of the project is refused outright,
so the frozen document cannot move by accident).

**Seeding it from scratch** (only if the row is gone), with the source from the
`test-fixtures` branch (§4):

```bash
git worktree add /tmp/fixtures test-fixtures

PID=$(curl -s -X POST https://staging.getassureai.com/api/projects \
  -H 'Content-Type: application/json' -d '{"title":"workspace"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')

curl -s -X POST "https://staging.getassureai.com/api/projects/$PID/substrate/upload" \
  -F 'file=@/tmp/fixtures/docs/demo/insurance-boston-real-estate/assets/naic-underwriting-policy-redacted.md'
```

…then open the shell on that project (project switcher, top-left) and run the intent (§5).
The upload returns the substrate `id`; the compile must be given it as `substrate_file_ids`
(the shell does this from the project's source list — `included: true`). The `{"title":"demo"}`
there is data, not copy — it reproduces the row the switcher already lists, so §6's recovery
step still matches it.

**Resetting it — last resort.** This deletes the frozen document and the 44 revisions with it;
§3 and §9 say why not to do it for demo day. It is here for the case where the row is already
gone. The demo project accumulates a version per run and per audit, and a rehearsal that ends
mid-run still lands a version:

```bash
PID=demo-3235f5
curl -s -X DELETE "https://staging.getassureai.com/api/projects/$PID"    # deletes the projects row (see below)
# then re-seed as above, and re-select the project in the shell
```

On the box, a project's current document is
`/home/ubuntu/assure-prototype/prompt_matrix/projects/<project_id>/document.jdf`; version
history, substrate rows and JDF rows live in the single SQLite file
`/home/ubuntu/assure-prototype/prompt_matrix/history.sqlite` (`substrate_vault`,
`jdf_documents`, `projects`). **The API call still deletes the `projects` row only, and part of
the cascade now fires** — re-measured 2026-09-18 after `97246c9` (`PRAGMA foreign_keys=ON` in
`history.py:_apply_pragmas`, the one place every connection passes through). Three of the nine
tables a project delete can strand declare the cascade and now honour it: `jdf_revisions` and
`substrate_vault` (`FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE`) and
`daily_compile_limits`. **Six do not** — `jdf_documents`, `node_revisions`, `audit_log`,
`project_budgets`, `token_ledger_entries`, `pipeline_cache` carry a `project_id` with **no
`FOREIGN KEY` clause at all**, so no pragma can reach them; they are a schema migration, not a
pragma. Orphan census of the live DB: 6 `jdf_documents`, 2 `node_revisions`, 45 `audit_log`,
10 `project_budgets`, 58 `token_ledger_entries`, 15 `pipeline_cache` rows reference a project
id that no longer exists (the residue of deletes before the pragma; the counts include
pre-`97246c9` deletions). The switcher reads `projects`, so the reset still works for the demo
— the project is gone from the shell and re-seeding makes a new id — but the orphan rows and
the project directory stay on disk. Clear them by id if the box is to be handed over clean.

### 3a. Visible copy — the 2026-09-18 hygiene pass

Two display strings changed, and nothing else:

| Surface | Before | After |
|---|---|---|
| The switcher's own default label, and the title a shell-created project gets (`prototype/index.html:75`, `prototype/shell.js` ×7) | `Untitled` | `workspace` |
| The About page's lead on the buyer's questions (`prototype/about.html:136`) | "Twelve questions. Each one has a specific answer the **demo** can show." | "…the **workspace** can show." |

The internal `demo` names stay: `demo-redhat-btn`, the `demo-chip` class, `demo.redhat.*`,
`landing.demo.*`, the `assure:demo-redhat-audit` event. They are identifiers a client never
reads, and they are not copy.

**The row kept the title `demo` through this pass; a later pass renamed it.** This pass stopped
short of the row on purpose: `projects.title` is a persisted value — the switcher draws it
straight from `GET /api/projects` — so rewriting it is a data migration (backup first, owner's
call), not a copy change. A separate pass then ran that one-row UPDATE at **2026-09-18T20:12Z**:
`demo-3235f5.title` is now `workspace`, `current_version` unchanged at **45**, and no other
column was touched. Measured immediately after: `GET /api/projects` returns
`{"id":"demo-3235f5","title":"workspace","current_version":45}`, and the switcher row draws that
title. The inverse, if the owner ever wants the old label back:

```bash
# the inverse of that rename — not run by any pass:
# sqlite3 prompt_matrix/history.sqlite "UPDATE projects SET title='demo' WHERE id='demo-3235f5';"
```

Verified after the pass against the **served** bytes on the box (not the source tree): `GET /`
line 75 reads `<span id="project-current-name">workspace</span>`; served `shell.js` carries
7 × `"workspace"` and 0 × `"Untitled"`; served `about.html` carries "the workspace can show" and
**zero** occurrences of `demo`.

**Why the quoted md5 can move.** The box checkout is the deployment target for sibling changes
too, so any sibling deploy rewrites `prototype/shell.js` — and the §2 pre-flight value with it.
Re-measure before an audience; the strings above are the durable check.

### 3b. The post-pass live check, 2026-09-18

Read-only on the box, through the served path (`:8891`, gate header) and the app's SQLite:

| Item | Value |
|---|---|
| `GET /api/projects` | `200`, 126 projects, `demo-3235f5` present |
| The row | `title="demo"` (as measured; renamed to `workspace` later — §3a), `current_version=44`, `created_at=2026-09-18 14:09:34`, `node_count=3`, `source_count=1`, `status=ready_to_export` — **this table is the pre-`v45` measurement**; §3c and §10.1 carry the current state |
| Rows that reference it | `jdf_revisions` **44** (v1–v44 as measured then; 45 rows now, v1–v45 — §3b note below), `jdf_documents` 1, `substrate_vault` 1, `node_revisions` 2, `pipeline_cache` 47, `token_ledger_entries` 192, `audit_log` 63, `user_activity_log` 356 |
| `GET /api/projects/demo-3235f5/jdf` → `200` | 1 section (*Massachusetts Commercial Real Estate Underwriting Obligations Summary*), **3** paragraphs, **3** anchored, 5 confidence spans |
| `POST …/draft/stream` with the §5 intent and source `sub-d3eab1f0fa9c486d` | `200`; frames `status → compiled → verified → complete` (`ok: true`); **cache-warm** (`omp_cached: true`, `cache_key=ast:demo-3235f5:1e9c9516`, 0.02 s); `provenance_stats` = eligible **3** / anchored **3** / supported **2** / partial **1** / unsupported **0** / unverified **0**; **no new revision**. Those are the **v44** document's numbers — that key holds the v44-era payload (§10.1 states the scope) |

The compile was deliberately **not** re-run cold: a cold run would move the document §10.1
pins, and §7 forbids re-running live to move a counter. No row was deleted by this pass.

**Since the 20:03Z landing a cold compile of this project is refused anyway** — the guard is
documented as a mechanism in **§3c**, with the live refusal row, the reason code and the set's
current membership. So the **warm** compile in the table above is the demo path and stays
repeatable, a deliberate recompile takes `force=true` and lands an audit entry, and
`demo-3235f5.current_version` stays **45** unless the demo-state owner says otherwise.

**A peer pass then landed that very `v45` (2026-09-18 20:00:12).** It is the 2A retention
pass's acceptance check ("verify the demo document compiles"): one cold `DRAFT_STREAM` on
`demo-3235f5` with the §5 intent and the project's included source (`sub-d3eab1f0fa9c486d`).
What it wrote, per that pass: `jdf_revisions` 44→45 (`compile`, change summary *"Underwriting
Obligations for Boston Commercial Property Insurance"*), `pipeline_cache` 47→53,
`token_ledger_entries` 192→199, `audit_log` 63→64, `user_activity_log` 356→357,
`projects.current_version` 44→45 (row now `node_count=7`, `lock_count=8`). **Nothing deleted**,
and the document still serves. Everything in the table above stands as measured before it, in a
check that ran in the same hour.

**Settled, 2026-09-18: `v45` is the demo state.** The 2A pass's acceptance compile is the state
the project ships from, and §10.1 pins it. Restoring `v44` is no longer an open demo-state
decision: **`v44` is retained in `jdf_revisions` and is reachable through the version
navigator** — that is why nothing was pruned, and a presenter can show the previous state.
Verified read-only through the served gate: `GET /api/projects/demo-3235f5/jdf` returns the
current document (**7** paragraphs), `…/jdf?version=45` returns `200` with the same 7
paragraphs (`para-6692b9cd12aa` … `para-038c1dc33053`), and `…/jdf?version=44` returns `200`
with the retained v44 tree — 3 paragraphs, `para-0dc42860abf3`, `para-1c4f277b0c79`,
`para-f32e4c85e0a5`. So the "re-measure §10.1 against v45 or restore v44" framing this
paragraph used to carry is closed: §10.1 has been re-measured **against v45**, and the v44
numbers live there too, labelled.

### 3c. The frozen-project guard — a mechanism, not a convention

Two layers protect the demo artefact, and only the first one is durable.

- **The mechanism — a cold compile of a frozen project is refused, in the compile path.**
  `routers/draft.py` carries the set, reads it from the environment, and refuses before any
  model call:

  ```python
  _FROZEN_PROJECTS_DEFAULT = "demo-3235f5,a4-d3-1789759434-4a6346"   # draft.py:262

  def frozen_projects() -> set[str]:                                  # draft.py:271
      raw = os.environ.get("ASSURE_FROZEN_PROJECTS", _FROZEN_PROJECTS_DEFAULT)
      return {part.strip() for part in raw.split(",") if part.strip()}

  def frozen_cold_compile_blocked(*, project_id, cached_hit, force=False) -> str:   # :277
  ```

  The refusal carries the reason code **`frozen_project_cold_compile`** (`draft.py:891`, `:898`)
  and the message:

  > This project holds a frozen document: a compile now would replace the revision it is pinned
  > to. Its cached compile still runs unchanged. To recompile it deliberately, send `force=true`
  > and the override is written to the audit log.

  **The set is env-driven, so the owner changes it without a code change and without a deploy.**
  The unit sets only `ASSURE_ENV=staging` (+`PATH`), `/etc/assure/*.env` does not name
  `ASSURE_FROZEN_PROJECTS`, and the running process does not carry it — so the default above is
  what applies today. To change it: one line in `/home/ubuntu/assure-prototype/.env.staging`
  (loaded at startup by `keys.py:load_keys()`, precedence shell env > `.env.staging` > `.env`)
  and `systemctl restart assure-prototype`. Narrowing it to the demo alone is
  `ASSURE_FROZEN_PROJECTS=demo-3235f5`.
- **The convention — "do not compile the demo", which is the prose in this runbook.** It is the
  layer that **already failed once**: a well-intentioned verification pass ran a cold compile
  and moved the artefact from `v44` to `v45` (§3b). A reader who has only the convention has no
  protection at all, which is why the guard sits in the compile path and not in the prose.

**The set as it stands (measured on the box, 2026-09-18).** It is a **superset of what the demo
needs**, and the owner can narrow it:

| id | why it is in the set |
|---|---|
| `demo-3235f5` | the demo artefact — the case the guard was written for |
| `a4-d3-1789759434-4a6346` | **not part of the demo.** It is the document the determinism gate names, and a sibling's probes compiled it repeatedly: it stands at `current_version=27` (27 revisions, `updated_at` `2026-09-18 20:00:59`, title `A4 D3 1789759434`) after those probes moved it off v17. Nothing the demo shows depends on it, so it is in the set as a precaution rather than a requirement — **narrow the set if the extra id is not wanted**. |

**Both halves of the mechanism were observed live, on the box's own `audit_log`** (not read off
the source):

| what | audit row | result |
|---|---|---|
| a **cold** compile of the demo | `DRAFT_STREAM`, `2026-09-18 20:11:08` | `success=0`, `error_message="compile refused: frozen_project_cold_compile"`, `details={"rejection": "frozen_project_cold_compile", "cache_key": "ast:demo-3235f5:1b397914"}` — a cache **miss** is refused; nothing persisted, no revision written |
| the demo's own **warm** path | `DRAFT_STREAM`, `2026-09-18 20:11:01` | `success=1`, `details={"cache_hit": true, "cache_key": "ast:demo-3235f5:1e9c9516"}` — a cache hit is *not* a cold compile, so the guard lets the demo path through and writes nothing; `current_version` held at 45 |

The distinction a reader has to hold before touching this: **a cache hit replays and writes
nothing; only a cold compile can move the artefact.** `force=true` is the deliberate override,
recorded in the audit log as an authorised act rather than a failure.

**The pattern for verifying this project: measure the artefact without writing to it.** The 2D
pass verified the demo exactly that way — the export path plus cache-key arithmetic, no compile
— and every number in §10 was measured from the live DB and the served export paths. §7 forbids
re-running live to move a counter, and §3b's version history is the evidence that the rule is
load-bearing rather than decorative.

### 3d. The one orphan `foreign_key_check` could see — cleared 2026-09-18

**`PRAGMA foreign_key_check` reads zero on the demo box**, and one row was deleted to make it so.
The row was `audit_log` **rowid 424**, written **2026-09-18 20:01:59**:

| Column | Value |
|---|---|
| `id` | `0d46a035a9914b968351a02e3bd78ae3` |
| `request_id` | `732abd92-4778-4c40-b5db-269eeb0e75ca` |
| `project_id` | `nope` — no project of that id has ever existed |
| `action` | `RETRIEVAL_SEARCH`, `success` 1 |
| `details` | `{"node_id": "nope", "query": "x", "cards": 0, "rejected": 10}` |

**It is a probe artefact, not application traffic** — a one-character query against a project id
of `nope` — and it was the only orphan in the database: the group-by over `audit_log.project_id
not in (select id from projects)` returned exactly `[('nope', 1)]` across the table's 494 rows.

**Why the insert was accepted.** `sqlite3` defaults `foreign_keys` to OFF per connection, and
`prompt_matrix/lib/logger.py:111` opens the audit trail's own connection without the shared
pragmas — `PRAGMA foreign_keys=ON` lives in `_apply_pragmas` (`prompt_matrix/history.py:35`),
which `history.get_db`, `db/pool.py:66` and `db/connection.py:776,962` apply and this path does
not. That is `Migrate2A`'s flagged "last path that can still create an orphan", and this row is
that path reaching production data. The code fix — the pragma plus a route-level project check —
commits separately from this note; nothing about it was deployed here.

**The deletion was backed up first, by the sqlite3 backup API rather than `cp`.** The stated `cp`
method was changed deliberately: a `cp` of a live SQLite file can capture a torn state mid-write,
and `sqlite3` is not installed on the box, so the page-consistent snapshot is Python's
`connection.backup()`, the same method as `.backup` in the CLI:

```bash
# the safe method actually used — NOT cp:
#   python -c "import sqlite3; s=sqlite3.connect(DB); d=sqlite3.connect(DEST); s.backup(d)"
# then, asserting the affected count is exactly 1 (rolled back otherwise):
#   DELETE FROM audit_log WHERE rowid=424;
```

| Item | Value |
|---|---|
| Backup | `/home/ubuntu/backups/history.sqlite.20260918T201725Z.pre-cleanup.bak` (23 572 480 bytes, outside the checkout) |
| Backup verified | `integrity_check` `ok`; rowid **424** present with the identity above; `audit_log` **494** rows |
| Affected rows | **1** |
| `PRAGMA foreign_key_check` after | **0 rows** |
| Orphan group-by after | **empty** |
| Whole-DB census | `audit_log` **494 → 493**, rowid **424** removed, **no row modified**; the other **31** tables byte-identical by sha256 — including `projects`, `jdf_revisions`, `token_ledger_entries` and `user_activity_log` |
| Demo untouched | `demo-3235f5` still `title='workspace'`, `current_version` **45**; `GET /api/projects/demo-3235f5/jdf` → `200`, `ok: true`; `/health` `200`, `sqlite: ok` |

No service was restarted and nothing was deployed for this — it is one row delete and this note.
**`audit_log` held 494 rows, not the 487 quoted when the cleanup was ordered**: sibling passes
kept appending audit entries between the two measurements, so the measured pair is **494 → 493**.

**This note supersedes one clause of §3 and §7 without editing them.** Both still read as though
`audit_log` "carries a `project_id` with no `FOREIGN KEY` clause at all … so no pragma can reach
them". That was true of the *repo* DDL (`db/connection.py:863-876`) and is **no longer true of the
box**: migration 25 (`scripts/aws/migrate_fk_constraints.py`, applied 2026-09-18 15:17:07) rebuilt
`audit_log` — it is in the migration's `TABLES`, and `rebuild_with_fk` appends `FOREIGN KEY
(project_id) REFERENCES projects(id) ON DELETE CASCADE`. Measured on the box: the live DDL carries
that clause, `PRAGMA foreign_key_list(audit_log)` returns it, a fresh connection reads
`foreign_keys=0`, and after `=ON` the same `project_id='nope'` insert is **rejected with
`FOREIGN KEY constraint failed`**. So on the migrated box the pragma is load-bearing for
`audit_log`, and once the audit-path fix ships here an unknown project id makes the insert raise
and be swallowed by the logger's `except` — the entry is **dropped, not recorded**, which is why
the route-level check has to sit in front of the write. §3's and §7's pre-migration counts and
their "six tables" clause are unowned by this pass and left as measured there.

---

## 4. The fixture — now on the `test-fixtures` branch

**Location: branch `test-fixtures`, commit `e1d589c`** (path unchanged —
`docs/demo/insurance-boston-real-estate/assets/`, 14 files). The fixtures left this branch on
2026-09-18 so a production checkout carries no demo material; the project row they seed did
**not** move (§3).

Retrieve them:

```bash
git worktree add /tmp/fixtures test-fixtures
ls /tmp/fixtures/docs/demo/insurance-boston-real-estate/assets/
```

`git show test-fixtures:docs/demo/insurance-boston-real-estate/assets/<file>` prints a single
file without a checkout. In that asset directory:
— `naic-underwriting-policy-redacted.md` (1.7 KB, the source the demo uses),
`naic-underwriting-policy-redacted.pdf` (same text as PDF, for the Ingest path),
`rating-engine-config.json` / `-corrected.json` (the drift), `one-pager.md`, `slides.md`.

`fixtures/real-estate-insurance/` in this checkout is **empty** — do not look for the demo
source there.

**Reset the fixture:** there is nothing on this branch to reset — the files are on
`test-fixtures`, and `git worktree add /tmp/fixtures test-fixtures` is the whole recovery. The
only fixture state on the box is the preserved project's uploaded substrate row
(`sub-d3eab1f0fa9c486d`); do not re-upload it before an audience (§3).

---

## 5. The demo itself — the 9-step integration to record

The fallback video is **a screen recording of the 9-step integration against the deployed
build** (steps 1–9 of the Golden Path, `docs/user-experience.md` §3), recorded at 1440×900,
zoom 100 %, on `https://staging.getassureai.com`, at revision `8115964`. Record the screen plus
the URL bar so the build is identifiable. Shot list — every step ends on a visible artefact:

1. Open the shell on the project the switcher lists as `workspace` (id `demo-3235f5`; only the
   label reads workspace, the id keeps its internal name) — the Main document and the dock are on
   screen.
2. Write the intent in the dock and submit — the exact text in
   `scripts/aws/_demo_intent.txt`, and it is **typed into the dock**: nothing reads the file at
   compile time, so the file is the source of truth for the presenter, not for the pipeline:
   *"Summarize the Massachusetts commercial real estate underwriting obligations in the source.
   Report the wind/hail deductible percentage, the maximum liability in USD, and the inspection
   interval in months. Cite each figure."*
   The earlier wording also asked to "compare policy language to the rating engine configuration
   and note any drift". That clause needs **two** sources; on a policy-only project the engine's
   own numbers are nowhere in the vault, so the comparison invites invention. Add the clause only
   when `rating-engine-config.json` is uploaded alongside the policy.
3. Watch the four STAGES tick (Retrieve → Draft → Anchor → Verify) and the header line fill;
   the document streams into the canvas.
4. Read the compiler pane: YOUR ASK, **ROUTED TO** (the model `cost_governance` chose), and the
   compiled prompt.
5. Select a paragraph — the Evidence pane shows its ledger tail and confidence band.
6. Run **Run Red-Hat** in the Evidence pane; it runs ~20–35 s and lands a finding ("Last run: 1
   finding, Ns"). This is the adversarial pass.
7. Move to the **Math check (Z3)** tab; show the numeric verdict for the node (or "No Z3
   findings for this node").
8. Compare the claim counters (Anchored / Supported / Partial / Unverified) against the
   document, and the SOURCES manifest's "anchored N of M". On the current document the counters
   read **Anchored 5 / Supported 2 / Partial 2 / Unsupported 1** with the export's `anchors: 5`
   (§10.1) — those are the numbers to expect, not round numbers.
9. Navigate versions with ◀ ▶ in the header — the label moves (e.g. `v3 of 4`) and the document
   body changes — then **Export** (audit PDF) for the dossier.

Record it once now; re-record only if the revision changes. Keep it where the demo machine can
reach it without the box being healthy (a local file or a phone), because the whole point is
that it plays when staging does not.

---

## 6. The three most likely failure points

1. **The model provider fails mid-run** — the stream stops with `Failed to fetch` (network out)
   or an error frame, the prompt pane reads `(compiler unavailable)`, and no document lands.
   *Recovery:* say so, reload the page, and re-run the same intent — the run is stateless until
   it completes, and a completed run is persisted server-side. If it fails twice, play the
   fallback video (§5) and continue from step 8 (counters and export still work on the last
   persisted version).
2. **The demo project is not the one on screen** (wrong project, or a rehearsal left it
   mid-state). The header shows the project name next to "Assure".
   *Recovery:* project switcher → `workspace` (the row's label; the id is still `demo-3235f5`);
   if its document looks wrong, reset it (§3) before the
   audience arrives — never reset it live.
3. **A reload during the demo** (the presenter hits refresh, the laptop sleeps). After a reload
   the document, the counters, the confidence spans and the version chip all come back, but
   **ROUTED TO falls back to "Awaiting route" and the COMPILED PROMPT is empty**, and the
   selected paragraph is no longer selected.
   *Recovery:* re-run the intent (steps 2–4) — 30–40 s — and carry on; do not apologise for it,
   the pane is honest about its state.

---

## 7. Known limits to avoid live

- **Do not zoom.** The 3-pane grid is fixed (48 px rail | 280 px | 1fr | 320 px, plus the right
  rail), so the centre document column is `viewport − 696 px`: it is 744 px at 1440, **328 px at
  1024** (below the 400 px canvas floor in `docs/frozen-shell.md`), **24 px at 720** — which is
   what 1440 px at 200 % zoom becomes — and **0 px at 375**, where the right pane is clipped off
  screen with no horizontal scrollbar. `shell.css` carries only `prefers-reduced-motion` media
  queries; there are no width breakpoints. Keep the demo window at ≥1440×900, zoom 100 %.
- **The STAGES list is in the left pane.** If the left pane is collapsed, the only way to
  re-open it is **Cmd+B** (there is no on-screen control once it is collapsed; Cmd+. toggles
  "focus mode" only when both panes are collapsed).
- **Red-Hat is per node and one at a time.** While a run is in flight every other node's Run
  button is disabled ("A Red-Hat run is in progress on another node."). Section nodes are not
  auditable — say "paragraph" when you point at one.
- **The source scan is a fixed nine-phrase list.** `services/compile_guard.py:FLAG_PHRASES`
  matches nine literal phrases (`ignore previous`, `disregard the above`, `output only`, …), so
  a novel phrasing of an order is not caught by the scan — the pre-validator catches the
  injected token instead, by refusing the draft it produces.
- **A refusal replaces the stream within 2 s.** When the validator refuses, the streamed text
  is replaced by a single refusal card ("could not be grounded in the source. Nothing was
  saved."), not an error frame: measured `error` frame → card **6 ms**, last streamed token →
  card **1016 ms**, and the refused run leaves Draft marked failed with no later stage ticked.
  It is not a hang — do not wait for it, and do not re-run it live.

**The four limits that ship with this demo** — say these plainly if anyone asks:

- **The compile is reproducible, and the frozen document is still the thing to show.** The
  cause was routing, not sampling: OpenRouter load-balances one model id across several upstream
  providers and they do not agree at `temperature=0.0`. The compile now pins its upstream
  (`provider: {order: ["Alibaba"], allow_fallbacks: false}`, `routers/draft.py`), and three
  compiles of one intent over one source, compile cache cleared before each, persisted
  byte-identical documents (§10.3a). **Fallback if a live compile ever varies: do not re-run it
  in front of the customer — open the pre-compiled document and demonstrate from there.** Never
  re-run live to "fix" a counter; the frozen document's numbers are the ones to quote.
- **The FK deletion covers 3 of 9 tables.** With `PRAGMA foreign_keys=ON`, `jdf_revisions`,
  `substrate_vault` and `daily_compile_limits` cascade; the other six tables that carry a
  `project_id` declare no foreign key, so a project delete strands their rows (§3). The switcher
  reads `projects`, so the reset itself still works — the orphan rows and the project directory
  stay on disk.
- **The Red-Hat locator ships with this demo.** A finding carries the paragraph it is about
  (`annotations.redhat[].node_id`) and clicking one — in the finding list in the right pane, or
  on the chip beside the paragraph — scrolls the document to that paragraph and highlights it
  (§10.2, §10.3c). Findings written before this change carry no `node_id`; they still render
  against the node they are placed on.
- **The demo document is short by design — and the current one is fuller than the last.** The
  pinned `v45` counts **7 eligible / 5 anchored** (2 supported, 2 partial, 1 unsupported, §10.1);
  the retained `v44` document counted **3 eligible / 3 anchored**. Either way the count is the
  gate's eligible-claim count, not the model's word count: when a draft opens with a lead-in
  sentence the gate counts it and the ratio drops (measured on the v44-era draft: 4 eligible /
  3 anchored). That is the claim floor working, not a shortfall.

**What the shell does with a compile that cannot finish** (measured on staging 2026-09-18, peer pass):

- **No source attached → refused before the first stage.** Reason `no_source_attached`, plain message
  *"Upload a source first. Assure grounds every claim against the source you provide."*, three frames
  in **0.017 s**, **no model call, nothing persisted**. Before this: 139 token frames / 3755 chars over
  11.1 s with the refusal only at 11.7 s.
- **The dock's Submit is disabled while the project has no source**, labelled *"Add a source to enable
  the compile"*, and the empty column reads *"Add a source to compile. Assure grounds every claim
  against the source you provide."*
- **A compile that stops without a terminal frame** clears the streamed draft and shows *"This compile
  stopped before the document was verified. Nothing was saved."* — verified by killing the app unit
  mid-stream. A client is never left reading half a document.
- **The in-band refusal card is unchanged** and measured **1.37 s** from the last token to the card.

**Do not be surprised by the bridge exemption.** `compile_guard.is_question_to_source_bridge` exempts a
draft whose opening sentence names the source or asks it a question (`compile_guard.py:200-228`), and
that exemption is deliberate — a document that asks rather than asserts has no claimed subject to
ground. The consequence for demo day: **a mismatched source whose draft opens "the provided source
material contains…" is not refused and persists as a document with ANCHORED 0.** Reproduced twice.
The About claim *"a document that fails grounding is refused outright"* is true of the drafts the
grounding rules reach; this shape reaches past them by design, and the counters are where it shows
(Anchored 0). If the demo walks a source/question mismatch, expect that state, not a refusal card.

**Operating the tunnel (learned the hard way, 2026-09-18).** Do **not** send `SIGHUP` to
`cloudflared` to reload `/etc/cloudflared/config.yml`. On this box (`cloudflared` 2026.8.3,
`cloudflared --no-autoupdate --config /etc/cloudflared/config.yml tunnel run`) the signal makes
it **exit cleanly** instead of reloading, and with the then-current `Restart=on-failure` systemd
did not bring it back: `staging.` and `prototype.` answered **502** for 14 s (measured
17:13:41 → 17:13:55 UTC) while the origin on `:8891` stayed healthy. Reload the documented way —
start a second cloudflared on the new file, confirm health, then stop the first — and expect
long-lived streams to drop when the first instance stops. The unit is now `Restart=always`
(`/etc/systemd/system/cloudflared.service`, backup `.bak-20260918T171604Z`), so a clean exit can
no longer leave the edge silently down.

---

## 8. Auth and data — what to say, truthfully

Say these, in these words; they are the measured facts, not aspirations.

- **Auth: staging is open.** No credential is required to read the shell, to list projects, to
  read any project's document, to create or delete a project, or to upload a source. Measured:
  `GET /api/projects` → `200` and `POST /api/projects` → `201` with no cookie or token, and
  `GET /api/projects/<id>/jdf` → `200`. This is `ASSURE_ENV=staging` with
  `ASSURE_ENFORCE_OWNERSHIP` unset: `middleware.py:ownership_enforced()` returns false when the
  env var is unset and Clerk is not required. **Do not put a real client's documents in it.**
- **Tenancy: one instance, one tenant.** Projects are addressed by id only and ownership is not
  enforced on staging, so anyone with the URL can act on any project — there is no per-user
  isolation to promise today. Treat the demo instance as a shared room.
- **Where the data goes.** Uploaded source text is extracted on the box and persisted there:
  the substrate row in `prompt_matrix/history.sqlite` (`substrate_vault`), a copy in the OMP
  memory DB as a `vault` memory (`/home/ubuntu/.omp/omp.db`), and the compiled document in
  `prompt_matrix/projects/<project_id>/document.jdf` plus `jdf_documents`. Nothing on this box
  writes to R2 or S3 — there is no such configuration in `.env.staging`. **Model calls do leave
  the instance**: the compile sends the intent and the retrieved source context to the routed
  provider (OpenRouter by default on this box — ROUTED TO names it), so the source text reaches
  that provider. OMP recall is **not project-scoped** — a recall can return another project's
  file (observed live: `GET /api/omp/recall?key=deductible` returned the NAIC policy text from a
  different project) — so the memory layer is instance-wide, not per-project.

---

## 9. After the demo

**Do not reset the demo project** — it is DB-preserved and frozen at `v45` (§3, §3b). Delete any
*other* project created during the demo (`DELETE /api/projects/<id>`), and note anything that
failed in the feedback template, which now lives on the fixtures branch:

```bash
git show test-fixtures:docs/demo/insurance-boston-real-estate/feedback-template.md
```

The wave's own scratch projects are the obvious operands of that instruction — observed on the
box on 2026-09-18, all created between 20:04Z and 20:21Z: `2c-fetch-demo`, `2c-fetch-api`,
`2c-hostile-probe`, `2c-gap-fail`, `2d-round-trip-200933-1020c6`, `2d-shape-probe`,
`1c-det-copy-0f68e5-1eff24`, `auth1d-owner-a-0813e4`, `auth1d-owner-a-89a60a`. Deleting them is
**not** done by this pass — §9 governs what happens after the demo, and §9a records the rest of
the cleanup.

### 9a. Post-demo cleanup — WRITTEN, NOT ACTIONED

**Nothing in this section has been done.** It is a checklist for after the demo, recorded now
because the reasons are only visible while the wave's context is fresh. **The demo stays
reachable on the current gate key until it is over**: do not rotate or retire the key before
then, or the presenter's door locks behind them.

| # | Action | Why |
|---|---|---|
| 1 | **Rotate or retire the bearer gate key** — `SHELL_ACCESS_KEY` in `/etc/assure/shell-access.env`. Retire it, or scope it to `/api` only. | The gate key is a **full-access credential, not a limited one**. It is a single shared secret compared constant-time with no identity attached (`prototype/dev-server.py:109-115`), and it is the outer door for every public hostname: `/api/*` is proxied upstream and everything else is served from disk (`:39-52`, `:314`). The one authorization layer that exists — `check_project_ownership` — reads a **Clerk identity** (`middleware.py:57-67`: `current_user_id()` / `session["clerk_user_id"]`, then `current_role() == ROLE_ADMIN`), so the shared-key operator, who has no Clerk identity, cannot be *role-gated* by it. The key is all-or-nothing: it opens the gate for whoever holds it, or it is closed — there is no role-aware setting to dial it down to, because there is no identity to attach the role to. That is acceptable while it is one door among several during a demo; it is not what you want as **the** door, which is what "Clerk is the only door" would make it. Retire it, or scope it to `/api` only. |
| 2 | **Rotate the Brave Search API key** (`BRAVE_API_KEY`). | The value was transmitted in chat, so it is a known-exposed credential even though the file is mode 600. It matters more than a normal key because the same variable lights up a second grounding path (§9b) — a leaked key buys someone the ability to rewrite paragraphs. |
| 3 | **Delete the two Clerk test users:** `auth1d.a@`, `auth1d.b@`. | They are the auth pass's fixtures, and they exist **in Clerk, not in the app DB** — verified read-only: no row in any `history.sqlite` table matches either address, so the delete is a Clerk-console action and there is no local row to clean up afterwards. Leaving them is leaving two working sign-ins on a shared instance. |
| 4 | **Confirm no test credentials remain in `.env.staging`.** | It is the file the app loads as staging config (`keys.py:load_keys()`), and the wave added keys to it while probing. Also update `.env.staging.example` and the CHANGELOG: `BRAVE_API_KEY` is in `.env.staging` but is **not** named in the example file, and the CHANGELOG was not updated for it — so the next reader cannot discover the variable or its rotation duty from the tracked files. |

### 9b. Recorded, not actioned — do not improvise these

Three live findings that change behaviour or promise more than the code does. **None is fixed by
this pass** — they touch product surfaces or copy the owner approves — and the first is now being
landed as a separate change (§9b item 1).

**1. Two grounding paths with opposite epistemics, and the About page promises the honest one.**
The `BRAVE_API_KEY` install lit up a second path, because `services/ground_node.py:search_brave_web`
reads the same variable (`:36-37`). With the key present, `ground_node()` takes `method="search"`
once the snippets total **≥ 100 characters** (`:157-163`; 1304 measured live) and hands the
paragraph plus Brave snippets to **`SEARCH_MODEL` = `deepseek/deepseek-chat`** (`:22`) with
`SEARCH_PROMPT` = *"Rewrite the following paragraph using the provided search snippets. Correct
factual errors…"* (`:26-29`). It stamps `meta.search_attribution` (`:101`) — and **creates no
source row, ingests nothing, and never re-runs the anchoring gate**. The paragraph stops looking
unanchored without being grounded. Meanwhile the About page says: *"Any claim the model generated
that cannot be grounded in the source is visually isolated and flagged as unanchored — never
hidden in flowing prose"* (`prototype/about.html:141`). **The live behaviour contradicts the
published sentence.**

*Accepted, and in flight — not by this pass:* **2C owns unanchored claims, and the owner has
ordered `/ground` retired.** The retrieval pass is landing the removal as a separate change after
the current box bytes are committed, so record this as **pending landing**: the behaviour
described above is what the served build does until that change is live. Removal scope, for the
reader who has to check it landed: delete `routers/ground_routes.py` and `services/ground_node.py`,
drop the registration in `web.py`, delete the Z3 branch of `renderEvidenceFooter` plus
`performGrounding` in `shell.js` (~35 lines), and move `search_brave_web` into
`services/web_retrieval.py` so 2C reuses the client. **Not improvised by this pass** because it
changes a Z3 surface and product copy the owner approves.

**2. The fetch path refuses PDFs — so the most authoritative documents are the ones it cannot
read.** `services/web_retrieval.py:337-340` raises `unsupported_content_type` for any response
whose `content-type` is outside `TEXT_CONTENT_TYPES`, and much of the model-law allowlist is
served as PDFs. The fix exists and is already in the stack (the Textract/Docling extractor used
for uploads); the gap is recorded rather than silently omitted. Related, same pass: a fetch of
`federalregister.gov` **redirects to `unblock.federalregister.gov` and stores a CAPTCHA page** —
honest, in that nothing anchors against it and the note says so, but a block-page guard would be
better than storing it.

**3. The retrieval validation fix is committed but NOT live — the box is not yet clean.**
`routers/retrieval_routes.py` now guards project existence **before any work or audit write**
(`_project_exists` at `:77`, returning `404 "project not found"` at `:95`, `:123`, `:144`,
`:202`), the audit connection applies `_apply_pragmas`, and a dropped audit insert is surfaced as
`AUDIT ROW DROPPED — …` at ERROR (`lib/logger.py:173`). Verified against the committed code:
project `nope` gives 404 on search/fetch/reject/gap with no audit rows before or after, and real
traffic is unaffected. **But the running service is older than the file**: the box's
`prompt_matrix/routers/retrieval_routes.py` has mtime **2026-09-18 20:18:16** while
`assure-prototype.service` started **2026-09-18 20:14:32** (`NRestarts=0`), so the process holds
the pre-fix bytes. The file on disk equals the committed bytes (md5 `80a41f277cdc0b5f318db1a3ea11aeb3`
both on the box and in this checkout). **Say this plainly if asked: the fix is landed, not yet
served.** The `AUDIT ROW DROPPED` surfacing matters because with the pragma enforcing on a
migrated box a bad insert raises, and without it the failure would vanish.

---

## 10. The demo state in one page (hand this to the insurance team)

Measured read-only on `i-03e39eccc57572191`, box HEAD `a536a52` (`prototype/shell-skeleton`,
2026-09-18T20:2xZ — the HEAD moves as passes land docs and evidence, so treat the project id and
the tree hash below as the durable identity), project `demo-3235f5`, source
`sub-d3eab1f0fa9c486d` (`naic-underwriting-policy-redacted.md`, 1 page). Nothing below is
re-run for the handout — it is read out of the persisted document and the DB.

### 10.1 The frozen document — `v45`

**The demo state is `v45`** (`projects.current_version = 45`; row `updated_at`
`2026-09-18 20:00:12`, title `workspace` — §3a). Source of truth:
`prompt_matrix/projects/demo-3235f5/document.jdf`, **JSON-equal** to `jdf_documents.tree_json`
and **byte-identical** to `jdf_revisions` **v45** (`mutation_type` `compile`, created
`2026-09-18 20:00:12`, change summary *"Underwriting Obligations for Boston Commercial Property
Insurance"*; 24 019 bytes of tree). Identity from the app's own hasher
(`db/document_lock_repository.hash_jdf_tree`, sha256 over the canonical JSON):

**`document_sha256` = `e05ac8d1297753c5bb8b07a37fb1cae25df59cf2ccb53c0afffb5998117a68d8`, and the
v45 chain head resolves to that same value — `document_sha256 == chain_head_sha256`.** Print it
with the handout if anyone wants to re-derive it.

| | |
|---|---|
| Sections | **1** — *Underwriting Obligations for Boston Commercial Property Insurance* |
| Paragraphs — and `node_count` as the switcher counts it (`flatten_nodes`) | **7** |
| `lock_count` (`truth_ledger` keys) | **8** |
| Eligible claims | **7** |
| Anchored | **5** |
| Supported (`entailment.verdict == "yes"`) | **2** |
| Partial (`verdict == "partial"`) | **2** |
| Unsupported (`verdict == "no"`) | **1** |
| Unverified | **0** |
| Unanchored (`eligible − anchored`) | **2** |
| Confidence spans | **17** |
| Gate | `gate_status` **pass**, `z3_status` **PASS**; no Red-Hat run on this compile (`redhat_count: 0`) |
| Drafting model | `openrouter/qwen/qwen3-next-80b-a3b-instruct` — from `projects.last_compiled_json.gate.measure` |
| Export identity | the `format=jdf` export reports `anchors: 5` (`audit_log` `EXPORT_JDF`, `2026-09-18 20:09:33`) |

**Where those numbers come from, and the scope of each** — because a different set has been
carried in this runbook as "v45's". The figures above are the **persisted document's**, and they
agree across every scope measured on the box:

| scope | what it is | value |
|---|---|---|
| `projects.last_compiled_json.gate.provenance_stats` | the gate block persisted with the project at compile time | eligible **7** / anchored **5** / supported **2** / partial **2** / unsupported **1** / unanchored **2** / unverified **0** |
| `pipeline_cache` key `ast:demo-3235f5:1784a441` → `verified.provenance_stats` | the v45 compile's own payload (updated `2026-09-18 20:00:12`) | identical |
| the live tree, counted with the gate's own counter (`services/audit_summary._provenance_counts`) | `jdf_documents.tree_json` = `jdf_revisions` v45 = `document.jdf` | identical |
| the export | node anchors in the `.jdf` sidecar | **5** — matches `anchored` |

**The set that does *not* belong to `v45` is `eligible 3 / anchored 3 / supported 2 / partial 1 /
unsupported 0`.** That is **`v44`'s**, and it is not a different *scope* of the same document —
it is a different document, which is why it is not a contradiction. `pipeline_cache` key
`ast:demo-3235f5:1e9c9516` (updated `2026-09-18 16:49:54`, the v44-era payload) reports exactly
those numbers and its payload carries the 3 v44 paragraphs; the retained `jdf_revisions` v44 row
counts 3 eligible / 3 anchored with `truth_ledger` 4 keys. §3b's warm-compile row quotes 3/3
because that run replayed that key — **a cache hit replays the payload the key holds, not the
current document**, so a replayed compile can show the previous document's counters while the
persisted project stands at `v45`. **`v45` is 7 / 5 / 2 / 2 / 1; `v44` is 3 / 3 / 2 / 1 / 0.**

**`v44` is retained, and the version navigator reaches it.** Nothing was pruned: `jdf_revisions`
holds 45 rows (v1–v45). Verified read-only through the served gate — `GET
/api/projects/demo-3235f5/jdf?version=44` → `200` with 3 paragraphs (`para-0dc42860abf3`,
`para-1c4f277b0c79`, `para-f32e4c85e0a5`, the table below), `…?version=45` → `200` with the same
7 paragraphs as the current document, and `…/jdf` with no `version` → the v45 tree. **A presenter
can show the previous state with ◀ in the header** (§5 step 9); that is why the retention
matters. The retained v44 tree hashes to
`12f03ccd228041aba68b9f579f86daf880a2ef605b21f5d48e71352b0bd9fb31`.

**`v45` is a valid compile of the same document against the same source — not a corruption, and
not a restore candidate.** It is the 2A pass's acceptance compile (§3b): one cold `DRAFT_STREAM`
with the §5 intent and the project's included source `sub-d3eab1f0fa9c486d`, `success=1`,
19 858 ms, model `openrouter/qwen/qwen3-next-80b-a3b-instruct`, `z3_status: PASS`, gate `pass`,
no refusal. It anchors **5 of its 7** eligible paragraphs where v44 anchored 3 of 3, and its body
is fuller — 7 paragraphs and 17 confidence spans against v44's 3 and 5 — from the same source,
the same intent and the same routed model. Nothing about it is degraded: the paragraph it adds
that v44 did not have (`para-038c1dc33053`, the DORA-style claim) is honestly marked
**unsupported** rather than hidden, and the gate still reads `pass`. What changed is that the
draft came back longer.

The seven paragraphs, with the document's own verdicts (`node.meta.provenance.entailment`, read
from the live tree):

| # | node | state | anchored to |
|---|---|---|---|
| 1 | `para-6692b9cd12aa` | **unanchored** | — (the lead-in sentence: no provenance row) |
| 2 | `para-62f65a3abd34` | **partial** | `naic-underwriting-policy-redacted.md` p.1 — *"the wind/hail deductible is **2 percent** of insured value at each location"* |
| 3 | `para-3876bd6ab726` | **supported** | same, §2 — *"Maximum general liability per occurrence shall not exceed **$2,000,000 USD**…"* |
| 4 | `para-ea43d6a772ea` | **supported** | same, §4 — *"Physical inspection of occupied commercial properties is required at least once every **24 months**"* |
| 5 | `para-b382cef9f49b` | **partial** | same, §5 — *"Underwriters must verify that bound terms match the active rating engine JSON configuration"* |
| 6 | `para-c79845f70b8a` | **unanchored** | — (the bulleted parameter recap: no provenance row) |
| 7 | `para-038c1dc33053` | **unsupported** | same, §6 — *"## 6 Regulatory context (DORA-style controls)"* |

Three of them in the sidecar's own one-line form, which is what a reader opening the file sees:
`para-3876bd6ab726 [supported] anchor=naic-underwriting-policy-redacted.md p=1`,
`para-038c1dc33053 [unsupported]`, `para-6692b9cd12aa [unanchored]`.

**The retained `v44` document** (`jdf_revisions` v44, `mutation_type` `compile`, created
`2026-09-18 16:49:54`, change summary *"Massachusetts Commercial Real Estate Underwriting
Obligations Summary"*): 1 section, **3** paragraphs, **3 anchored**, 2 supported, 1 partial,
0 unsupported, 0 unanchored, 5 confidence spans, `truth_ledger` 4 keys. Per paragraph (verdict
and reasoning read from `node.meta.provenance.entailment`):

| # | node | anchored to | verdict | the check's own reason |
|---|---|---|---|---|
| 1 | `para-0dc42860abf3` | `naic-underwriting-policy-redacted.md p.1` — *"the wind/hail deductible is **2 percent** of insured value at each location"* | **partial** | "confirms the 2 percent deductible for the specified counties but does not mention commercial real estate properties or Section 3, so those material elements are unsupported" |
| 2 | `para-1c4f277b0c79` | same, §2 — *"Maximum general liability per occurrence shall not exceed **$2,000,000 USD**…"* | **yes** | "directly states the exact limit of $2,000,000 USD and the condition for exceeding it…" |
| 3 | `para-f32e4c85e0a5` | same, §4 — window sentences 7-8 (inspection + vacancy referral) | **yes** | "directly states both that occupied commercial properties require inspection at least every 24 months and that vacant properties exceeding 60 consecutive days require referral…" |

The **v44** gate counts 3 eligible claims, and all three anchor — 100 %, above the 80 % floor.
**The floor only bites when the draft opens with a lead-in sentence** — and v45 is what that looks
like at scale: 7 eligible, 5 anchored, 71 %. The five-run measurement below is the **v44-era**
measurement and is kept as measured, because it is what explains the shape. In it, two runs
drafted an extra introductory paragraph — *"The underwriting policy for commercial real estate in
Massachusetts, effective January 1, 2026, establishes the following key obligations:"*
(`para-26374bfdce83` in run 1, `para-e303112d9fdf` in run 2) — which lifted `eligible` to 4 and
left `anchored` at 3, i.e. 75 %. Nothing is wrong with the source: the paragraph does not clear
the anchor matcher's 0.60 coefficient, because its evidence (*"**Jurisdiction:** Massachusetts"*,
*"**Effective:** January 1, 2026"*) sits in source **header lines that tokenize to 3 content
tokens** and so are not eligible anchor windows at all (`_MIN_ANCHOR_OVERLAP = 4`,
`models/jdf.py:857`), while its only ≥0.60 window is rejected by the number guard. When the draft
went straight to the three claims (runs 3-5, and the document that was frozen then) the counters
read 3 / 3 — and v45 is the same lead-in-plus-claims shape that produced 7 eligible, 5 anchored,
with the two unanchored paragraphs being exactly that lead-in and the bulleted recap.

### 10.2 The Red-Hat finding, in full

Where it lives: `jdf_revisions` **v4** of `demo-3235f5` (`mutation_type` `redhat_audit`,
`target_node_id` `para-272b2de84877`, created `2026-09-18 14:25:56`), under
`body[*].children[*].annotations.redhat[0]` as
`{"id": "crit-0bb78763ea96", "text": …, "status": "open"}`. Persisted findings are attached to
the node the audit ran on, and this one is quoted verbatim:

> **Verdict: partial match.** The source supports the core numeric and geographic rule, but the
> claim adds several assertions the source sentence does not carry.
>
> **Source wording relied on:**
> > "For coastal and high-wind exposure zones (Suffolk, Norfolk, Essex counties), the wind/hail
> > deductible is **2 percent** of insured value at each location"
>
> ## Findings
>
> 1. **Unsupported scope limitation: "commercial properties."**
>    The source says "For coastal and high-wind exposure zones," not "For commercial
>    properties." The claim narrows the rule to commercial properties without support in the
>    supplied source. If the larger guideline elsewhere defines commercial scope, the claim
>    should cite that; on the supplied source alone, this is an unsupported qualifier.
>
> 2. **Unsupported universality: "applies uniformly across all policies bound under this
>    guideline."**
>    The source does not say "uniformly," "all policies," or "bound under this guideline." It
>    states a deductible for listed zones. A stated rule is not the same as an exceptionless
>    application to every policy. This is an overstatement.
>
> 3. **Unsupported cross-reference: "explicitly tied to geographic exposure as defined in the
>    policy."**
>    The source ties the deductible to "coastal and high-wind exposure zones," but it does not
>    say "explicitly tied" or "as defined in the policy." It also appears to be an underwriting
>    guideline, not necessarily the policy itself. The claim conflates guideline language with
>    a policy definition.
>
> 4. **Potential overbreadth on geography.**
>    The source lists "(Suffolk, Norfolk, Essex counties)" as the zones, but it does not
>    explicitly say those counties are wholly coastal/high-wind zones. The claim's phrasing
>    could be read as applying countywide. If the actual exposure zones are partial-county or
>    sub-county, the claim overgeneralizes.
>
> ## Other concrete risks
>
> - **"Insured value" is undefined.** The source does not specify whether this means building
>   value, contents, blanket limit, total insured value, or some other valuation basis. The
>   claim repeats the phrase without resolving it.
> - **No treatment of exceptions, minimums, maximums, or endorsements.** The claim's
>   "uniformly" language implies no exceptions. The source does not rule out dollar minimums,
>   per-occurrence caps, endorsements, or underwriting exceptions.
> - **"Set at" vs. "is."** Minor, but the source says the deductible "is" 2 percent, not that it
>   is "set at" 2 percent. Usually immaterial, but in a legal/underwriting context wording can
>   matter.
> - **"Threshold" terminology.** The source calls it a "wind/hail deductible," not a
>   "threshold." The claim's term may be acceptable shorthand, but it is not source language.
> - **Redacted source risk.** The provenance gate points to a redacted underwriting-policy
>   file. Missing context could change scope, applicability, or definitions. The claim should
>   flag that it relies on a redacted p.1.
> - **Guideline vs. policy.** The claim says "as defined in the policy," but the source is an
>   underwriting guideline. If the actual policy defines coastal/high-wind zones differently,
>   the claim may misstate the binding document.
>
> **Bottom line:** The 2 percent figure and the listed counties are supported. The claim's first
> sentence is mostly supported except for the unsupported "commercial properties" limitation.
> The second sentence is materially overbroad and unsupported: "uniformly across all policies
> bound under this guideline" and "explicitly tied to geographic exposure as defined in the
> policy" are not in the source.

Two things to say out loud with it, because they are true of the artefact:

- **The finding is not in the document that is frozen now.** `run_redhat_pipeline` persists it
  only for a node-scoped audit (`save_jdf_revision(..., mutation_type="redhat_audit",
  target_node_id=…)`, `routers/draft.py:1186-1192`), and the latest compile (**v45**, and v44
  before it) replaced the tree, so `para-272b2de84877` — and with it the finding — is not in any
  current document; the live tree's paragraphs are the seven listed in §10.1. The finding is real
  and persisted, but recoverable from the revision, not from the live tree; run Red-Hat live
  (step 6) if you want it on screen.
- **The finding object now carries `node_id`, so the locator works.** Findings written from this
  change onward are `{id, node_id, status, text}` (`models/jdf.py:attach_redhat_annotation`;
  `JDFRedhatAnnotation` declares the field, which is what lets it survive `parse_document` into
  SQLite) and clicking one scrolls the document to that paragraph. Every finding written before
  it is `{id, status, text}` — 14 in the box's current documents, 58 across all revisions,
  measured 2026-09-18 — and for those the node is still carried by *placement* inside
  `annotations.redhat`. Verified live: an audit on `phase-c-nodeid-*` persisted
  `{id: "crit-90e96fb778f9", node_id: "para-67d43586fe19", status: "open", …}` (scratch project,
  since deleted).

### 10.3 Four honest notes for the runbook

**(a) The compile is reproducible now — the cause was provider routing, not sampling.**
The five-run measurement that used to stand here (five compiles of one demo intent, all under
`temperature=0.0`, produced **five different drafts** while the outgoing payload was identical)
was real, and it was diagnosed on 2026-09-18. `temperature=0.0` **does** reach the request body
— `litellm.utils.get_optional_params(...)["temperature"]` is `0.0`, and the captured outgoing
JSON carries it — so the sampling parameter was never the problem. The provider was: OpenRouter
load-balances one model id across upstream providers, and they do not agree at greedy decoding.
Three streaming calls each, real compile system prompt, same seedless payload:

| upstream | distinct drafts / 3 | re-measured at 8 |
|---|---|---|
| DeepInfra | 3 | — |
| Parasail | 3 | — |
| Google | 3 | — |
| Alibaba | **1** | **1 / 8** |
| Novita | **1** | **1 / 8** |
| no pin (control) | 3 | **8 / 8** |

A `seed` does not fix it: `seed=42` through litellm gave 3/3 distinct, and raw with `seed=42`
gave 2/3 — the seed is honoured *inside* one provider, and routing is what varies. So the compile
pins the provider instead: `provider: {order: ["Alibaba"], allow_fallbacks: false}`
(`routers/draft.py`). `allow_fallbacks` stays **False** because Alibaba and Novita are each
stable but do not agree with each other (sha `25bbbc92…` vs `585f6e9d…`) — a fallback would
silently swap the document, so a provider outage reads as a failed compile instead.

End-to-end on the deployed box: **three compiles of one intent over one source, compile cache
cleared before each, persisted byte-identical documents** — `draft_text` sha `b1bdbb1ba826d4cc`
and document sha `d5fadc69fd846407` on all three runs, with revisions 1/2/3 proving each was a
real pipeline run and not a cache replay. Consequence: a re-run reproduces the document. Keep
showing the frozen one anyway; it is the artefact the counters below describe.

**(b) The document is short by design — and the retained v44 is the short one.** The check refuses
to overclaim: every sentence that asserts a figure is matched to a source window, and the gate's
eligible count is the number of paragraphs that clear the claim floor — not the model's word
count. The retained `v44` document is the short case (3 claims); the pinned `v45` is the fuller
one (7 eligible / 5 anchored, §10.1), and its two unanchored paragraphs are the lead-in sentence
and the bulleted recap. A longer draft was measured too, at the v44 era: eligible 4 / anchored 3,
because the extra paragraph was a lead-in sentence the source cannot vouch for. Prefer the
document whose numbers are about the source; on the current one the counters read 5 anchored,
2 supported, 2 partial, 1 unsupported, and that unsupported paragraph is listed in §10.1 — do not
present it as verified.

**(c) The locator shipped with this release.** A finding is displayed against the paragraph it was
run on, now carries that paragraph's id (`annotations.redhat[].node_id`), and clicking it scrolls
the document to that paragraph and highlights it — from the finding list in the right pane and
from the chip beside the paragraph (`prototype/shell.js:_locateNode`, declared at `:2300` and
wired into both click paths at `:4887` and `:5186`; the pass renamed it from `_scrollToNode`, so
an older citation to that name is stale).

Two things had to be true for the claim, and only the first was in the earlier plan. The
annotation has to **carry** the id, and the id has to **survive validation into SQLite**.
`JDFRedhatAnnotation` declares `extra="ignore"`, so `parse_document` — which every write goes
through — silently dropped a `node_id` the write site had just set. Isolated on the box:
`['id','node_id','status','text']` immediately after `attach_redhat_annotation`,
`['id','status','text']` after `parse_document`. The field is now declared, and a live audit
persisted `node_id` on the finding (§10.2).

**(d) The document round-trips, and the export is not a one-way door.** A fresh project imported
the exported sidecar, and the round trip held. Two of the confirmations are in the box's own
audit log, so they are checkable without trusting this document: `JDF_IMPORT` on project
`2d-round-trip-200933-1020c6` (`2026-09-18 20:09:33`, `success=1`) carries
`details={"anchors_total": 5, "anchors_resolved": 5, "document_sha256_matches": true}`. The rest
is the 2D pass's live check, reported and not re-run here: verification states identical,
entailment verdicts identical (**yes 2 / partial 2 / no 1**), node anchors byte-identical
**7 of 7**, and a re-export hashing to the same `document_sha256` as §10.1. That is what
"export the dossier" means concretely.

**The limitation that ships with it, stated rather than hidden:** an answer citing **two figures
from two different source positions is refused** by the pre-existing numeric guard. The floors
are out of scope for this pass and are unchanged — this is the guard behaving as written, not a
defect introduced for the demo. Practically: a question that asks for two numbers from two places
will refuse instead of answering, so ask for them one at a time in front of the audience.

---

## 11. Multi-agent discipline — the rules this wave earned

**Why these cluster.** All three collisions on 2026-09-18 — one shared tree's HEAD twice, and one
shared worktree — were the same shape: **a resource one writer assumed it owned and a second
writer did not know about.** Every one was caught by an agent noticing that its own state had
moved underneath it. **The detection worked; the assumptions failed.** The four rules below exist
so the next wave relies on the second and not the first.

**Each rule carries the incident that earned it, deliberately.** A rule without its reason gets
overruled by the next person who finds it inconvenient — which is exactly what happened to the
audit-path comment asserting `audit_log` declares no foreign key (true of the repo DDL, false of
the migrated box; reported to its author) and to §3's and §7's "six tables declare no foreign
key". Both are superseded in place by §3d rather than quietly deleted, so the reason survives the
rule.

### Rule 1 (required) — Rebase only branches you own

*Earned when a rebase rewrote five SHAs on a branch another agent had checked out with uncommitted
work in that worktree.* The edits survived **only because the hunks happened to apply cleanly** —
which is luck, not design, and luck does not survive a second occurrence. A rebase takes another
agent's uncommitted work **silently when it conflicts**: from git's perspective nothing went
wrong, so nothing reports it, and the loss surfaces as "my change is missing" hours later. If a
branch can have more than one writer, either own it explicitly (and say so) or take a fresh
branch — never rebase a branch that is someone else's working surface.

### Rule 2 (required) — Every result names its base commit and the md5 it was measured against

*Earned because `prototype/shell.js` moved through a long series of md5s in a single day.* Measured
here, and it is not hypothetical: **29 commits rewrote `prototype/shell.js` inside 2026-09-18
alone**, and at the time of writing the **committed blob is
`bef198bf2110389013f7964021f828c9` while the box serves
`1883c520e346e46f52b92c8c29971726`** — HEAD and the served build disagree right now. This runbook's
own front matter had already carried two values for the same file in one day (`9da3f758…` →
`ccb0f4e9…`, §2's table), and §3a keeps a paragraph about exactly this drift.

**A result without both a base and an md5 is attributable to no revision.** The form:

> verified on `<branch>` at `<md5>`, base `<sha>`

Never a bare *"verified."* — unattributable evidence is what this project spent the day deleting.

### Rule 3 (recommended) — One worktree per agent

*Earned when two agents landed on `/tmp/wt-postaudit` and `postaudit/fixes` from two dispatches*,
one of them discovering a rebase underneath its uncommitted edits. `git worktree add` gives each
agent its own HEAD and index; sharing one tree recreates Rule 1's hazard one level down, with the
added twist that the *paths* differ while the *refs* do not. Treat it as the default for any batch
with more than one writer.

### Rule 4 (recommended) — Name the branch alongside the tree, never the tree alone

*Earned when the local `.venv`'s editable install of `prompt_matrix` was found to follow HEAD* — so
**an import's meaning changes as branches move**, and a test run inside a worktree can read a
different branch's source than the one it believes it is testing. Measured in this checkout, and
it is worse than a single root:

| | |
|---|---|
| the editable finder's mapping | `MAPPING = {'prompt_matrix': '/Users/og/Untitled/prompt_matrix'}` — an **absolute path to the main tree**, not to whatever directory the interpreter is standing in (`__editable__.prompt_matrix-0.1.0.pth` → `__editable___prompt_matrix_0_1_0_finder`) |
| the bare-import fallback root | with `prompt_matrix/` on `sys.path`, a bare `import db` resolves to `/Users/og/Untitled/prompt_matrix/db/__init__.py` — the same main tree, which is the root the `from db.connection import …` fallbacks rely on |
| the hazard, demonstrated | running `/Users/og/Untitled/.venv/bin/python -c "import prompt_matrix; print(prompt_matrix.__file__)"` **from inside the worktree `/Users/og/Untitled.worktrees/cline-identity-query`** (branch `agents/cline-identity-query`, at `d5d6842`) printed **`/Users/og/Untitled/prompt_matrix/__init__.py`** — the run would have exercised the main tree's source while appearing to test that worktree's |

Because both roots point at one tree, neutralising one is not enough. **The practical form: every
test result names the branch, and where a worktree was used it prints the module file path to prove
which source the interpreter read** — `python -c "import prompt_matrix; print(prompt_matrix.__file__)"`,
and add the `prompt_matrix/` root to anything that exercises the bare-import fallbacks.
