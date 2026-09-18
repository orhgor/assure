# Demo-day runbook — the shell on staging

**Scope:** running the Boston RE insurance demo on the deployed shell prototype.
**Deployed revision:** `1cde23b` (`prototype/shell-skeleton`) on `i-03e39eccc57572191`.
**Verified:** 2026-09-18, against `https://staging.getassureai.com` (the box checkout is
`/home/ubuntu/assure-prototype`; `prototype/shell.js|shell.css|index.html` are byte-identical
to the committed revision — md5 `4cdd8ec5…`, `a6d63834…`, `698016cb…`).

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
`Untitled` (was `shell-proto`); the `prototype/index.html` md5 in the front matter above is
superseded by the value just quoted. Certificates need no action: the zone's universal cert
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
- **The demo document carries 3-4 claims by design.** The frozen document carries 3 (§10.1);
  when a draft opens with a lead-in sentence the gate counts 4 eligible / 3 anchored. That is
  the claim floor working, not a shortfall.

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

Reset or re-seed the demo project (§3), delete any project created during the demo
(`DELETE /api/projects/<id>`), and note anything that failed in
`docs/demo/insurance-boston-real-estate/feedback-template.md`.

---

## 10. The demo state in one page (hand this to the insurance team)

Measured read-only on `i-03e39eccc57572191`, box HEAD `34ce046`, project `demo-3235f5`, source
`sub-d3eab1f0fa9c486d` (`naic-underwriting-policy-redacted.md`, 1 page). Nothing below is
re-run for the handout — it is read out of the persisted document and the DB.

### 10.1 The frozen document

Source of truth: `prompt_matrix/projects/demo-3235f5/document.jdf`, byte-identical to
`jdf_documents.tree_json` and to `jdf_revisions` **v44** (`mutation_type` `compile`, created
`2026-09-18 16:49:54`).

| | |
|---|---|
| Sections | **1** — *Massachusetts Commercial Real Estate Underwriting Obligations Summary* |
| Paragraphs | **3** |
| Anchored | **3 of 3** — every paragraph carries one provenance row with an `extracted_quote` |
| Supported (`entailment.verdict == "yes"`) | **2** |
| Partial (`entailment.verdict == "partial"`) | **1** |
| Unverified / unsupported | **0** |
| Confidence spans | 5 |

Per paragraph (verdict and reasoning read from `node.meta.provenance.entailment`):

| # | node | anchored to | verdict | the check's own reason |
|---|---|---|---|---|
| 1 | `para-0dc42860abf3` | `naic-underwriting-policy-redacted.md p.1` — *"the wind/hail deductible is **2 percent** of insured value at each location"* | **partial** | "confirms the 2 percent deductible for the specified counties but does not mention commercial real estate properties or Section 3, so those material elements are unsupported" |
| 2 | `para-1c4f277b0c79` | same, §2 — *"Maximum general liability per occurrence shall not exceed **$2,000,000 USD**…"* | **yes** | "directly states the exact limit of $2,000,000 USD and the condition for exceeding it…" |
| 3 | `para-f32e4c85e0a5` | same, §4 — window sentences 7-8 (inspection + vacancy referral) | **yes** | "directly states both that occupied commercial properties require inspection at least every 24 months and that vacant properties exceeding 60 consecutive days require referral…" |

The gate counts 3 eligible claims here, and all three anchor — 100 %, above the 80 % floor.
**The floor only bites when the draft opens with a lead-in sentence.** In the five-run
measurement of the same intent (cache cleared before each run), two runs drafted an extra
introductory paragraph — *"The underwriting policy for commercial real estate in Massachusetts,
effective January 1, 2026, establishes the following key obligations:"* (`para-26374bfdce83`
in run 1, `para-e303112d9fdf` in run 2) — which lifted `eligible` to 4 and left `anchored` at
3, i.e. 75 %, below the floor. Nothing is wrong with the source: the paragraph does not clear
the anchor matcher's 0.60 coefficient, because its evidence (*"**Jurisdiction:** Massachusetts"*,
*"**Effective:** January 1, 2026"*) sits in source **header lines that tokenize to 3 content
tokens** and so are not eligible anchor windows at all (`_MIN_ANCHOR_OVERLAP = 4`,
`models/jdf.py:857`), while its only ≥0.60 window is rejected by the number guard. When the
draft goes straight to the three claims (runs 3-5, and the document that is frozen now) the
counters read 3 / 3.

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
  target_node_id=…)`, `routers/draft.py:1186-1192`), and the latest compile (v44) replaced the
  tree, so `para-272b2de84877` — and with it the finding — is no longer in the project's
  current document. The finding is real and persisted, but recoverable from the revision, not
  from the live tree; run Red-Hat live (step 6) if you want it on screen.
- **The finding object now carries `node_id`, so the locator works.** Findings written from this
  change onward are `{id, node_id, status, text}` (`models/jdf.py:attach_redhat_annotation`;
  `JDFRedhatAnnotation` declares the field, which is what lets it survive `parse_document` into
  SQLite) and clicking one scrolls the document to that paragraph. Every finding written before
  it is `{id, status, text}` — 14 in the box's current documents, 58 across all revisions,
  measured 2026-09-18 — and for those the node is still carried by *placement* inside
  `annotations.redhat`. Verified live: an audit on `phase-c-nodeid-*` persisted
  `{id: "crit-90e96fb778f9", node_id: "para-67d43586fe19", status: "open", …}` (scratch project,
  since deleted).

### 10.3 Three honest notes for the runbook

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

**(b) The document is short (3 claims) by design.** The check refuses to overclaim: every
sentence that asserts a figure is matched to a source window, and the gate's eligible count is
the number of paragraphs that clear the claim floor — not the model's word count. A longer
draft was measured too: it scored eligible 4 / anchored 3, because the extra paragraph was a
lead-in sentence the source cannot vouch for. Prefer the short document; it is the one whose
numbers are about the source.

**(c) The locator shipped with this release.** A finding is displayed against the paragraph it was
run on, now carries that paragraph's id (`annotations.redhat[].node_id`), and clicking it scrolls
the document to that paragraph and highlights it — from the finding list in the right pane and
from the chip beside the paragraph (`prototype/shell.js:_scrollToNode`, wired into both click
paths).

Two things had to be true for the claim, and only the first was in the earlier plan. The
annotation has to **carry** the id, and the id has to **survive validation into SQLite**.
`JDFRedhatAnnotation` declares `extra="ignore"`, so `parse_document` — which every write goes
through — silently dropped a `node_id` the write site had just set. Isolated on the box:
`['id','node_id','status','text']` immediately after `attach_redhat_annotation`,
`['id','status','text']` after `parse_document`. The field is now declared, and a live audit
persisted `node_id` on the finding (§10.2).
