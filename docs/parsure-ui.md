# Parsure review surfaces — what the reviewer sees, and why

The intake report (`docs/parsure.md` is its contract) is shown in three
places: the shell's **Fields** tab (`prototype/shell.js`, `#right-fields`),
the intake list `/parsing`, and the record page `/parsing/<report_id>`. This
page records the rules those surfaces share since the customer handoff of
2026-09-27 (`todos/assure_final_engineering_handoff.md` — "Evidence-State
Confusion", "Workflow Drift", "Prompt Contamination", "UI / Workflow Fixes",
"Signature Quality Is Too Vague", "Uncertain Document Has Empty Fields",
"Mixed-Bundle Extraction Is Sparse"). Backend fields named here are the
report contract of that date; a report saved before it lacks them and every
surface renders without them — nothing is invented for a missing key.

## Three sections, one rule (2026-09-27)

`services/parsure_view.field_section` is the server's copy; `_fieldSection`
in `shell.js` is the client's. Same order of tests:

| Section | Rule | Actions offered |
|---|---|---|
| **Not on this document** | `field_state == "not_found"`, or `evidence_state` in `not_on_document` / `unreadable`, or (older report) no value and nothing routed it to a person | **Enter value** only (the `correct` route). Never Accept, never Dispute. |
| **Needs review** | `field_extractor.field_needs_review`: `routing_action` not in `none` / `field_not_found`, or state `disputed` / `rejected` | Accept (when there is a value), Correct, Dispute; Resolve on an open dispute |
| **Found** | everything else | Correct, Dispute |

Not-found rows show what the search covered, from `evidence` when its `kind`
is `absent`: `len(searched_pages)` pages, `len(searched_node_ids)` nodes,
`searched_chars`, `readability`. A report without the block shows nothing —
no zeros.

**Counts.** The Fields-tab badge, the header chip and `review_summary.
fields_review` all count by `field_needs_review` (the queue's rule). Each
section head counts its own rows. The two can differ by exactly one case: an
absence on an *unreadable* page routes `retry_parsure` — it is in "Not on
this document" (the reviewer cannot act on the value) and it is counted as
review (the rescan is a person's decision). The record page prints both
("Need review: 3 · Found: 1 · Not on this document: 2") rather than hide one.

**Suspect values.** `evidence_state == "found_suspect"` (something was read
under the label, failed shape validation, `raw` kept, `value` null) shows the
`raw` text as a dotted-underlined suspect value, not "not found"; the row
detail prints `value_quality.words — basis` whenever `quality != valid`.

**Signature words.** `signature_quality.quality` is the 2026-09-27 vocabulary
(`present_clear`, `present_ambiguous`, `missing`, `stamp`, `printed_name`,
`unreadable`); the earlier one (`clear`, `faint`, `incomplete`, `stamped`,
`questionable`) still renders. `next_check` is printed under the row as
"Check next: …" on the shell and beside the signature words on the record —
an ambiguous signature always tells the reviewer what to look at.

**Verification words.** `verification_basis` is printed as the server wrote
it. A `verification_confidence` of `null` with no basis reads "Not verified".
A field the report never verified is never shown as verified
(`docs/anti-claims.md`).

## Source context on correction and dispute (2026-09-27)

Opening the editor (`_openEditor(name, "correct" | "dispute")`) renders a
source block above the input: the page (`source_span.page`, else
`evidence.page`), the quote (`raw`), the paragraph text from the loaded tree
when `tree_node_id` / `field_source_node_id` / `source_span.node_id` /
`evidence.node_id` matches a node (`findJdfNodeById` → `node.content` or
`node.title`), and **Show in document** — `_locateNode`, the same
scroll-to-top + 2.4 s `.is-located` flash the audit findings use. A field
with none of these says so ("No source location was recorded for this
field."); an absent field prints what was searched instead of a quote.

**Open original** (photos, scans, screenshots: `report.modality` in
`phone_photo` / `scanned_pdf` / `screenshot`, or `material_type` `photo` /
`screenshot`) links to
`GET /api/projects/<project_id>/documents/<document_id>/original`
(`routers/jdf_routes.py`). The shell probes that URL with `HEAD` once per
open report and renders the link only on 200. The route resolves the ingest
job through the intake report (`job_id`, else the job with the report's
`revision_id`), refuses a key from another project, and streams the object
from `services/object_store` with the file's content type, `inline`
disposition and `no-store`. It answers 404 `{"ok": false, "error": …}` when
no report names the document, no job has a key, or the object is gone.
**Today it is usually gone:** staged uploads are deleted after a successful
ingest (`tasks/parse_tasks.py`, `import-pdf`), so the link appears only for
documents whose original was retained (a failed job keeps its object; a
retention change would light the link up without a UI change). The web tier
streams bytes here; it never parses them.

## The mode gate (2026-09-27)

`SHELL.ui.mode ∈ review | prompt | upload`, derived — never set by a handler —
by `_syncUiMode()` in `shell.js`:

1. `prompt` while a draft / compile / inquire is in flight (`runInProgress`)
   or the dock's prompt input has focus;
2. `review` while the Fields tab is shown and the right pane is open;
3. `upload` while the intake card is the document column or an ingest job is
   active;
4. otherwise the last mode stands (initial: `upload` — nothing is loaded).

It is re-derived from `_applyRightView`, `_syncTrustState` (so every run,
job and report change), the intake card's build / clear, and the prompt's
focus / blur. The header shows it as a quiet text chip (`#shell-mode-chip`,
no colour block) and `body[data-ui-mode]` carries it for CSS.
`_fieldAction` and `_overrideType` re-derive and refuse outside `review` with
the row error "Switch to review mode to change fields"; nothing is posted.

**Audit (2026-09-27).** Every write to `__parsureReportId`, `__fieldsEditor`
and `__parsureFieldSel`, and every call of `_renderFieldsPanel`, was read:
they occur only in the Parsure loaders (`_loadParsure`, `_openParsureReport`,
project switch) and in the Fields panel's own handlers. The compile
`complete` / `verified` / `error` handlers (`handleEvent`) do not touch them;
`_afterParsureChange` re-renders the panel only from the report the server
just returned. No handler had to be stopped.

## List-first intake page (2026-09-27)

`/parsing` renders, in order: the summary line, **Documents** (one document
per row: type, date, quality, status, and — before any detail — the three
section counts as `N need review · M found · K not on document`, plus an
**Integrity ok / Integrity mismatch** chip when the report carries a
`snapshot`), then **Needs attention**, then **Extracted data**. The counts are
omitted for a document that yielded nothing or whose type does not fit the
page (those are one fact, "check the type", not N not-found rows). The
aggregate report left the page.

`/parsing/analytics?project_id=…` (`web.py: parsing_analytics_page`) is the
former Analytics block on its own page — same view model
(`_parsure_analytics_view`, `parsure_repository.analytics`), same rule: no
number the repository did not count; a workspace without intake reads "—"
and "No intake yet." The list links to it (`#analytics-link`) and the shell's
rail Analytics button points at it (`_syncRail`), workspace-aware. Both pages
share `templates/_parsure_style.html`.

## Record page (2026-09-27)

`/parsing/<report_id>` adds: the three sections (`#fields-review`,
`#fields-found`, `#fields-not_found`, each with its count and, for the last,
"Nothing to accept or dispute — enter the value if you have it."); the
**Why the type is uncertain** notice from `classification.uncertainty`
(reason codes, family, keywords matched, fields searched, fields found — each
"none" when empty, never omitted); the **Page coverage** table for mixed
bundles (`page_coverage`: page → segment → type → fields found →
readability); the **Snapshot** line (`snapshot.content_hash`, `stamped_at`,
and `integrity.ok` from `parsure_repository.verify_snapshot` — mismatch
prints the row's actual hash); the **Replay** section (attempts / max, stop
rule, eligibility and reasons, history, last proof, and a **Replay now**
button that posts `{actor}` to
`POST /api/projects/<id>/parsure/<rid>/replay`, reloads on `ok`, and on 409
shows the server's `error` sentence verbatim; disabled when not eligible or
the attempt limit is reached); and the **Low-quality pages** notice
(`quality_report.low_quality_pages`, threshold as `NN%`). Percentages are
`NN%` everywhere, as before.

## Strings

Every new sentence is a catalog key (`prompt_matrix/i18n.py`, all seven
locales): `shell.mode.*`, `shell.fields.mode_locked`, `shell.fields.section.*`,
`shell.fields.enter_value*`, `shell.fields.searched_*`, `shell.fields.
readability`, `shell.fields.verification*`, `shell.fields.not_verified`,
`shell.fields.next_check`, `shell.fields.source*`, `shell.fields.
show_in_document`, `shell.fields.open_original`, `shell.fields.suspect`,
`shell.fields.value_quality.*`, the new `shell.fields.reason.signature_*` /
`shell.quality.signature_*` words, and `parsing.*` for the server pages
(read through `strings.get(key, English)` in the templates). Visible copy on
the server pages must not use the system verbs (`tests/test_parsing_page.py:
FORBIDDEN_WORDS`). `ui_cache` is `assure-107`.

## Upload queue (2026-09-27)

Customer feedback of 2026-09-27: "while a file uploads there is no upload bar;
also I can upload multiple files, so a modal has to show progress." Both shell
file inputs (`#source-file-input` → `POST /api/projects/<id>/substrate/upload`,
`#dock-ingest-file` → `POST /api/projects/<id>/jdf/ingest`) are `multiple`;
every chosen file is a row in one queue (`_enqueueUploads` in `shell.js`).

* **The drawer** `#upload-modal` (index.html, outside `#modal-layer` so other
  dialogs never destroy it): file name, size, a bar, the stage line, the
  result or the error, and the overall line "3 of 5 done · 1 failed · 1
  cancelled". It auto-opens when an upload starts. **Close** hides it while
  uploads continue and the header chip `#upload-chip` ("Uploading 2…")
  reopens it; Escape closes it (inside the drawer, or anywhere when no
  blocking modal is up); focus goes to Close when a person opened it and back
  to the opener on close. At 640 px and below it is a bottom sheet.
* **Real progress.** The POST moved from `fetch` to `XMLHttpRequest` so
  `upload.onprogress` gives bytes sent / total: a determinate bar with
  "uploading 73% · 2.2 MB of 3.0 MB". Same route, same multipart `file`
  field, same-origin credentials, the same `{status, ok, j}` reading of the
  answer; the 202 + `task_id` contract is polled by the existing
  `_awaitTask`. At most **3** uploads move at once; the rest read "waiting".
* **After the bytes.** The row shows the ingest job's stage words
  (`_stageWord`: queued → fetching → reading → verifying → saving) from the
  task payload's `job.stage` / `job.status`, with `parser_name` and
  "OCR NN%" when the job carries them, over an **indeterminate** bar —
  parsing has no honest percentage and none is invented. The Sources list
  keeps its pending row meanwhile, as before.
* **Done / failed / cancelled.** Success runs the one post-upload tail
  (`_afterSourceUploaded`: source row, `SHELL.sources`, the quiet Parsure
  re-read, the manifest re-read) and paints the same result line as the
  Sources row ("N fields extracted · View", `_paintSourceResult`). The
  intake card is not touched by the queue — `_afterParsureChange →
  _syncDocState → _renderIntakeCard` owns it, guarded by its signature.
  Failure shows the server's `error` verbatim and **Retry**; a network error
  says so. **Cancel** aborts the XHR (or drops a waiting row); the row reads
  "Cancelled" with Retry. The 25 MB ceiling (`upload_limits.MAX_FILE_SIZE_MB`)
  and the accepted extensions are checked before any byte moves; the message
  is the row's error, never an alert.
* **Mode gate.** A queue in flight is `upload` mode for its duration
  (`_deriveUiMode` checks `_uploadsActive()` first); the derived mode returns
  when it drains.
* **Tests.** `tests/test_workbench_safeguards.py` asserts the `multiple`
  inputs, the XHR + `onprogress` upload on the unchanged routes, the cap, the
  ceiling constant, the indeterminate bar, the mode rule, and the
  `shell.upload.*` strings in all seven locales. Headless smoke (mock server
  reading the body slowly) at 1280 and 390 px: progress values rendered, one
  202 polled to "6 fields extracted · 2 need review · View", one 500 shown
  verbatim with Retry, the cap at 3, chip reopen, cancel; zero console errors.
  `ui_cache` is `assure-108`.

## Execution-model surfaces (plan Part 6, 2026-09-27)

`todos/fable_execution_plan.md` Part 6 / 11.4. The backend is adding
`report.execution`, per-field grounding, `report.tables[]`, `report.vision`,
`report.discovered_fields[]`, `graph_integrity.integrity_score` and pipeline
passes in `replay.history[]`; every surface below renders them when present and
says "not recorded" when absent. `services/parsure_view.py` holds the rules
(`field_badge`, `provenance_label`, `grounding_view`, `execution_view`,
`summary_breakdown`, `tables_view`, `vision_view`, `rerun_history_view`,
`discovered_fields_view`, `graph_integrity_view`); `shell.js` mirrors the first
five for the Fields tab.

* **6.1 Badges.** The row chip / record cell reads the plan's vocabulary as-is:
  Not found / Suspect / Unverified / Accepted / Review (plus Disputed / Rejected
  / Conflict when a person produced them). Order: a person's verdict, then not
  found, then `evidence_state == found_suspect`, then anything a person must
  look at, then accepted, else unverified. A small bar beside the confidence
  figure (`.conf-bar`, `aria-valuenow`) shows `extraction_confidence`; a
  provenance label reads `grounding_source` / `grounding_model` /
  `extraction_method` as "from label" / "from table" / "from model <id>" /
  "from image", with `provenance_confidence` beside it when the report has it.
* **6.2 Review summary.** Total / accepted / needs review / suspect / not found,
  counted by the badge rule (suspect is inside review). Actions: **Review all
  flagged** (selects / scrolls to the first row of the review section) and the
  existing exports. **There is no bulk-accept button, on purpose**: accepting a
  value nobody looked at is what the customer forbids; every acceptance is one
  row, one click, with its source shown.
* **6.3 Execution panel.** One row per step — LAYA, Z3 verification, Red-Hat
  draft, Red-Hat graph, LLM grounding, Rerun, Vision, Tables — with the status
  word, the block's counts (violations, findings, offered / grounded /
  rejected, passes, pages, facts…), its policy / model / ms and its `reason`.
  A step the report does not carry reads **not recorded**; a report without
  `execution` says so once and lists every step as not recorded — nothing is
  inferred from other blocks. The only timestamp is `execution.ran_at`, else
  `snapshot.stamped_at`, else "no timestamp recorded".
* **6.4 Grounding.** Each field with `grounding_quote` / `grounding_span` shows
  "Evidence: “quote” · Page N · chars a–b · <model>" (or the table cell). In
  the shell the quote is a button: the paragraph flashes (`_locateNode`) and
  the range is painted with the CSS Custom Highlight API
  (`::highlight(assure-grounding)`) over the rendered text runs — the span's
  chars when they read the quote back, else the quote's own position, else the
  span alone; without the API a single-text-node range is wrapped in `<mark>`
  and restored, a cross-element range keeps the flash. On the record page the
  quote links to the page's text block (`#page-N`).
* **6.6 Tables.** `report.tables[]` render as grids on the record page:
  caption, page, quality words and basis, the fields read from them; cells
  named in `fields_extracted` (row/col) carry the field's name as a mark and
  title. Bare field names without a cell are listed in the caption only.
* **6.5 Vision.** A panel per analyzed page: kind, quality status / flags /
  basis (poor quality reads red), and the facts table (name, value,
  confidence, evidence, model, bbox figures). Boxes are drawn over the image
  only when the stored original is still in the object store and the document
  is one page (`_original_available` in `web.py`); otherwise the list stands
  alone. `vision.reason` is printed when it did not run.
* **6.7 Rerun history.** `replay.history[]` as a table: when, trigger words
  ("LLM grounding pass", "Red-Hat targeted pass", "Replay", "Type changed by
  reviewer"), fields changed, found before → after, improved; `replay.passes`
  beside the attempts when the report records it. The Replay button stays.
* **Also:** discovered fields (unknown types) as their own small table; graph
  integrity (score, negative-evidence nodes, orphans) on the snapshot line;
  the list row shows a suspect count beside the three sections.
* **Tests.** `tests/test_parsing_page.py` (a report with every block, and an
  older one that must read "not recorded" everywhere and invent no
  provenance); `tests/test_workbench_safeguards.py` guards `assure-109`.
  Headless smoke at 1280 / 390 px: breakdown counts, badges, execution rows
  with counts and "not recorded", the grounding highlight over a
  markdown-rendered paragraph (exact span and quote search), Review all
  flagged, an older report's panel; zero console errors.

## Accounts (local user management, 2026-09-27)

Built against the backend contract of 2026-09-27 (`GET /api/auth/setup-status`,
`/api/auth/me`, `/api/auth/{setup,login,logout,accept-invitation,password}`,
`/api/team/*`, `/api/audit`) and mocked in the smoke; the shell and the pages
read whatever the app answers and hide nothing when there is no permissions
list (mode `off` / `clerk`, or an older app) — the server routes stay the
judge, this keeps the UI honest about them.

* **The gate** (`prototype/dev-server.py`). When `setup-status.mode ==
  "local"`, the shared `SHELL_ACCESS_KEY` is no longer the door: the gate
  serves `/setup` (while `needs_owner`: organisation, owner e-mail, display
  name, password twice, bootstrap token — the token is the one in the server's
  `.env`, which `scripts/gen-env.sh` prints once when it writes the file),
  `/signin` (e-mail + password, honours `?next=`, refuses an open redirect)
  and `/accept?token=` (display name + password). The forms post JSON to the
  proxied API with the session cookie; the app's `Set-Cookie` flows back
  because `_proxy` copies every upstream header except the hop-by-hop ones
  (the key cookie's own `Secure` logic is untouched). Documents (the shell,
  `team.html`, `audit.html`, the Flask pages) are served only when
  `/api/auth/me` knows the cookie; otherwise 302 to `/setup` or
  `/signin?next=`. `/auth` redirects to `/signin`. The gate forwards
  `X-Forwarded-For` (client IP appended to any chain) and `X-Forwarded-Proto`
  so audit rows carry the visitor's address. An unreachable app or one without
  the route reads as mode `off`: the key gate exactly as before; the cached
  status is dropped the moment a setup / login / logout answers 2xx.
* **Identity.** The header shows display name and role word as text
  (`#shell-identity`, hidden at ≤640 px; the More menu repeats it), with
  **Change password** and **Sign out** in More. `must_change_password` opens a
  password dialog with no close, no scrim click and no Escape until
  `POST /api/auth/password` answers ok.
* **Permissions.** `shell.js` writes `body[data-can-*]` from `me.permissions`
  and CSS hides Upload (`documents.upload`), delete source
  (`documents.delete`), Open original (`documents.download_original`), the
  export items (`exports.read`, dossier `exports.dossier`) and the type change
  (`classification.override`); the dock's Draft needs `compile.run`. Field
  actions stay visible but marked (`.is-denied`, `aria-disabled`) with the
  reason on hover and, on tap, in the row's error line: Accept →
  `fields.accept` (or `fields.accept_compliance` on a compliance-bound field),
  Correct / Enter value → `fields.correct`, Dispute → `fields.dispute`, Resolve
  → `disputes.resolve`. A 401 anywhere goes to `/signin?next=…`; a 403's
  sentence is shown where the action was. Nothing in the shell touches
  Sources credentials — there is no such panel in the prototype.
* **Team** (`team.html` + `account-pages.js`, rail item with `team.manage`):
  accounts (role select → `PATCH`, disable / enable, reset password → the
  temporary password shown once with Copy), invite form (e-mail + role → the
  token and accept link shown once with Copy, "E-mailed" / "Not e-mailed"),
  pending invitations with Revoke, active sessions with Revoke (the current one
  disabled). One's own row cannot change its role or status here (the server
  answers 409 too). Nothing shown once is stored anywhere.
* **Audit** (`audit.html`, rail item with `audit.read`): filters (workspace,
  actor, event type, since), rows of time · actor (name, role) · IP · event ·
  workspace / record / field · payload summary; the record id links to
  `/parsing/<report_id>`. The shell's Versions tab links here ("Full audit
  log") when the role may read it.
* **Server pages.** `web.py:_current_user_view` reads `g.assure_user` when the
  middleware sets it (else the Clerk session with no role) into every page as
  `me`; the header shows the identity, the record page hides **Replay now**
  without `reports.replay` and the type selector without
  `classification.override` (each replaced by the one-line reason), and
  history rows print the actor's role from the event's `actor_role` (or its
  payload).
* **Tests.** `tests/test_shell_gate.py` (pages, mode reading, routing rules,
  header forwarding), `tests/test_workbench_safeguards.py` (markup, gating
  code, Team/Audit pages, strings, `assure-110`), `tests/test_parsing_page.py`
  (identity and hidden controls with a fake `g.assure_user`; actor role on
  history rows). Headless smoke through the real gate against a mock app at
  1280 / 390 px: first run → `/setup`, bad token → server sentence, owner
  created → shell with identity; sign out; protected page → `/signin?next=`;
  423 and 401 sentences; temporary password → forced dialog (Escape kept it,
  mismatch named, change closed it); reviewer: uploads / exports / type change
  hidden, compliance Accept denied with the reason on tap, server 403 inline,
  Team page denied; owner: users, invite shown once, temporary password shown
  once, invitations / sessions, audit rows with record links, rail items.
  Zero script errors (the only console line is the browser logging the 423
  response itself).

## Claim verdicts in the shell and dossier (claim-v1, 2026-09-27)

Contract: `node.meta.provenance.claim = {policy, verdict: VERIFIED | UNSUPPORTED
| CONTRADICTED | INSUFFICIENT_EVIDENCE, reason, quote, quote_verbatim,
source_id, source_name, page, checks: {entailment, numeric, wording,
source_quality}, flags[]}`, `meta.claim_summary`, `confidenceSpans[].
numeric_consistency`; the Red-Hat compile frame may say `scheduled`. Older
trees carry none of it and keep the legacy entailment surfaces unchanged — the
two are never mixed on one document (`_docHasClaims`).

* **Anchor chips and colour.** The margin chip is the claim verdict word
  (Verified / Unsupported / Contradicted / Insufficient evidence): the
  persisted block's, or — for a paragraph without one — the verdict the
  server's own counter derives (`claim_policy.derive_claim(sources=None)`:
  nothing cited → Unsupported; cited with entailment `contradicts` →
  Contradicted, `partial`/`no` → Unsupported, else Insufficient evidence),
  so mark and tile agree in both languages (`tests/test_client_counters_
  parity.py` runs `_derivedCounts`, `_claimOf`, `_derivedClaimVerdict`,
  `_claimVerdictOf`, `_claimStateOf`, `_anchorStateOf` from `shell.js` in
  node against `audit_summary._provenance_counts`). `_derivedCounts` mirrors
  the server bucket for bucket: `supported == verified`, partial is never
  verified, `flagged` reads persisted blocks only (the wording term list is
  server-side). "Not assessed" remains only as the legend's no-colour entry. The `.jdf-p` carries `data-anchor-state` and
  `data-verdict-tone` (verified quiet green, contradicted red, unsupported /
  insufficient amber, none). Confidence spans are coloured from the same
  verdict (`.conf-verified` / `.conf-partial` / `.conf-contradicted`) — never
  from `score`, which is null now and was a numeric-lock figure before; the
  span's accessible name is "Claim: <verdict> · <numeric_consistency in
  words>". The legend names the verdicts. The 📎 cite chip appears only for a
  verbatim quote (`claim.quote` with `quote_verbatim: true`, or a citation
  row's own `extracted_quote`); `excerpt` is never read.
* **Evidence panel.** Header: verdict word · `claim-v1` · page (or "page not
  recorded" — never "page 1"); then the reason sentence, the quote in quotation
  marks with source name and page ("not verbatim" when so, "No verbatim quote
  recorded" when null), the numeric line ("Recomputed: 1,250 + 300 = 1,550 ·
  stated 1,550 ✓" or "… expected 1,550, stated 1,450 ✗ mismatch"), entailment
  and source-quality words, and wording / inconsistency flags as text badges
  ("guaranteed — not in the source", "Figure inconsistent with another
  claim"). The legacy citation rows follow.
* **Inspector confidence pane.** No "Citation confidence" or "Figures checked"
  numbers: the claim verdict with its reason, the numeric line, the entailment
  word, and each figure span's `numeric_consistency` in words.
* **Counts, bar, chip.** With a summary the 2×2 tiles hide and one line reads
  "N of M claims verified · c contradicted · u unsupported · i insufficient
  evidence · f flagged (· k inconsistencies)". The complete bar reads
  "Drafted · <that line>", and "✓ Drafted and verified against your sources"
  only when verified == total and flagged == 0. The header chip says
  "Verified" under the same condition, else "Review · <counts>" (contradicted
  first, red when any).
* **Red-Hat.** A compile frame with `status: "scheduled"` (or `pending`)
  polls `GET /api/projects/<id>/redhat/status` every 3 s (5 min ceiling) until
  `status.complete` (or a terminal word), then re-reads the tree so the
  findings land on their paragraphs; the panel says "scheduled — checking…"
  meanwhile. Each finding shows its `quote` verbatim (marked when
  `quote_verbatim` is not true).
* **Dossier and audit bundle** (`services/verification_dossier.py`,
  `services/audit_bundle.py`). `collect_claim_ledger(tree)` → one row per
  assessed claim (text, verdict, verbatim quote, source, page or "not
  recorded", entailment word, numeric detail, wording flags, unsupported terms,
  source quality, flags) and the summary (`meta.claim_summary`, else counted
  from the blocks, else None). `derive_trust_state(..., claim_summary=)`:
  any CONTRADICTED → not verified; verified < total or flagged > 0 → review
  required; only VERIFIED counts — a partial / INSUFFICIENT_EVIDENCE verdict is
  never verified (the legacy `supported` counter no longer feeds
  `accepted_total` when a summary exists). The dossier's summary row reads the
  counts by verdict, section "2. Claim ledger" is the table, and
  `verification_state.json` carries `claim_summary` and
  `sections.claim_ledger`. The audit bundle prints "Claims verified: V of T"
  (or, on an older tree, the legacy check's `yes`-only count named as such)
  and a "2b. Claim Ledger" table. Renderer policy unchanged (503 when none).
* **Tests.** `tests/test_verification_dossier.py` (ledger rows, numeric words,
  page not recorded, summary source, trust table from the summary, the dossier
  title from claims, the bundle's verified/total), `tests/test_workbench_
  safeguards.py` (no confidence-score words or classes, verdict words, no
  defaulted page, `/redhat/status`, `assure-111`, strings in every locale).
  Headless smoke at 1280 / 390 px: five paragraphs (one per verdict + one
  unassessed) → chips, tones, verdict-coloured spans with consistency words,
  cite chips only on verbatim quotes, chip "Review · 1 of 4 claims verified ·
  1 contradicted · …", the claim line replacing the tiles, the claim block per
  paragraph, "page not recorded"; no "Citation confidence", no "page 1"; zero
  console errors.
