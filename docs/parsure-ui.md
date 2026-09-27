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
