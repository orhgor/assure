# Demo-day runbook — the shell on staging

**Scope:** running the Boston RE insurance demo on the deployed shell prototype.
**Deployed revision:** `1cde23b` (`prototype/shell-skeleton`) on `i-03e39eccc57572191`.
**Verified:** 2026-09-18, against `https://staging.getassureai.com` (the box checkout is
`/home/ubuntu/assure-prototype`; `prototype/shell.js|shell.css|index.html` are byte-identical
to the committed revision — md5 `4cdd8ec5…`, `a6d63834…`, `698016cb…`).

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
| Build identity | `md5sum prototype/shell.js` in the box checkout | `4cdd8ec591c997d6154eb854d51c6da7` |
| Browser | 1440×900 or larger, **zoom 100 %** | see §7 |

All six commands above were run for this revision and returned exactly those values.

---

## 3. The demo project

**Project id: `demo-3235f5`** (title `demo`). It is the project the shell opens on the demo
machine (`localStorage.assure_project_id`), and it is the one the probes and the H6 run used.

**Seeding it from scratch** (only needed if it is deleted):

```bash
PID=$(curl -s -X POST https://staging.getassureai.com/api/projects \
  -H 'Content-Type: application/json' -d '{"title":"demo"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')

curl -s -X POST "https://staging.getassureai.com/api/projects/$PID/substrate/upload" \
  -F 'file=@docs/demo/insurance-boston-real-estate/assets/naic-underwriting-policy-redacted.md'
```

…then open the shell on that project (project switcher, top-left) and run the intent (§5).
The upload returns the substrate `id`; the compile must be given it as `substrate_file_ids`
(the shell does this from the project's source list — `included: true`).

**Resetting it** — the demo project accumulates a version per run and per audit, and a
rehearsal that ends mid-run still lands a version:

```bash
PID=demo-3235f5
curl -s -X DELETE "https://staging.getassureai.com/api/projects/$PID"    # deletes the projects row (see below)
# then re-seed as above, and re-select the project in the shell
```

On the box, a project's current document is
`/home/ubuntu/assure-prototype/prompt_matrix/projects/<project_id>/document.jdf`; version
history, substrate rows and JDF rows live in the single SQLite file
`/home/ubuntu/assure-prototype/prompt_matrix/history.sqlite` (`substrate_vault`,
`jdf_documents`, `projects`). **The API call deletes the `projects` row only** — measured
2026-09-18, `DELETE /api/projects/<id>` returned `200 {"ok":true}` and left 18 `jdf_revisions`,
1 `substrate_vault`, 1 `jdf_documents`, 3 `node_revisions`, 27 `audit_log`, 82
`token_ledger_entries` and 8 `pipeline_cache` rows plus the project directory behind, because
the `ON DELETE CASCADE` clauses never fire (SQLite runs with `PRAGMA foreign_keys` off). The
switcher reads `projects`, so the reset still works for the demo — the project is gone from the
shell and re-seeding makes a new id — but the orphan rows and directory stay on disk. Clear
them by id if the box is to be handed over clean.

---

## 4. The fixture

**Fixture path:** `docs/demo/insurance-boston-real-estate/assets/`
— `naic-underwriting-policy-redacted.md` (1.7 KB, the source the demo uses),
`naic-underwriting-policy-redacted.pdf` (same text as PDF, for the Ingest path),
`rating-engine-config.json` / `-corrected.json` (the drift), `one-pager.md`, `slides.md`.

`fixtures/real-estate-insurance/` in this checkout is **empty** — do not look for the demo
source there.

**Reset the fixture:** the files are committed, so `git checkout -- docs/demo/` restores them.
There is no fixture state on the box beyond the uploaded substrate row for the project — that
is reset by resetting the project (§3).

---

## 5. The demo itself — the 9-step integration to record

The fallback video is **a screen recording of the 9-step integration against the deployed
build** (steps 1–9 of the Golden Path, `docs/user-experience.md` §3), recorded at 1440×900,
zoom 100 %, on `https://staging.getassureai.com`, at revision `8115964`. Record the screen plus
the URL bar so the build is identifiable. Shot list — every step ends on a visible artefact:

1. Open the shell on project `demo` — the Main document and the dock are on screen.
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
   document, and the SOURCES manifest's "anchored N of M".
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
   *Recovery:* project switcher → `demo`; if its document looks wrong, reset it (§3) before the
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

Reset or re-seed the demo project (§3), delete any project created during the demo
(`DELETE /api/projects/<id>`), and note anything that failed in
`docs/demo/insurance-boston-real-estate/feedback-template.md`.
