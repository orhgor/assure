# Parsure V1 — what a document gets, and what the numbers mean

Parsure is the intake side of Assure. Every upload (PDF, scan, phone photo,
screenshot, PNG/JPEG/TIFF/BMP image, text file) leaves the worker with an
**intake report** next to its verified revision. Assure reads that report to
show quality, confidence and what needs a human. This page is the contract a
reviewer, an integrator or an auditor can hold us to. Spec:
[`assure_parsure_v1_icp_spec.md`](../assure_parsure_v1_icp_spec.md).

## The pipeline (one router, one report)

```
bytes ──► parser_router.route_intake ──► parse (jdf-cli / jdf-cli+tesseract / Textract)
             │  material_type, modality        │
             │  visual probe per page          ▼
             │  Laya triage (rules-v1)     verification (Z3, Red-Hat)  ──► revision saved
             ▼                                                                │
        intake dict ─────────────────────────────────────────► v1_orchestrator.run_after_parse
                                                                              │
                                              parsure_reports / _corrections / _disputes / _audit_events
```

`route_intake` is the only place that decides anything about a document's
path. Laya is a rule table over the probe's flags (`model: "rules-v1"`); it
annotates, it does not override. Verification is the shared Z3 + Red-Hat
layer; Parsure consumes its result and never re-verifies.

## The report

`GET /api/projects/<id>/parsure/<report_id>` (or `/latest`):

| Key | Meaning | How it is produced |
|---|---|---|
| `material_type`, `modality`, `source_kind` | what came in (`pdf/digital_pdf`, `photo/phone_photo`, `image/scanned_pdf`, `text_file/text`, `mixed_bundle/mixed`) | extension, PDF text-layer probe, image dimensions/EXIF; `handwritten_image`, `table` only from a client hint |
| `parser_name`, `parser_version` | `jdf-cli`, `jdf-cli+tesseract` (OCR), `textract`, `pymupdf` | the parser that actually ran |
| `pages[].quality_score` (0–1 or `null`) | how legible the page is | product of the measured factors: visual (blur, contrast, effective DPI), OCR confidence, parse coverage; text density only as a floor; `null` when nothing could be measured |
| `pages[].flags` | `low_res`, `blurry`, `low_contrast`, `no_text` | measured. **Not detected in V1:** skew, glare, noise, handwriting |
| `document_quality_score` | mean of scored pages | `null` when no page scored |
| `classification` | `document_type` (10 values incl. `uncertain`), `confidence` ≤ 0.9, `basis`, `override` | keyword heuristic; `uncertain` below 3 hits or on a tie; a reviewer's override replaces it |
| `fields[]` | the ICP fields for that type | see below |
| `plausibility[]` | 6 arithmetic rules for auto insurance | premium > 0, premium ≤ 20 % of liability, deductibles 0–5000, dates ordered, term ≤ 12 months, VIN year vs vehicle year |
| `conflicts[]` | `policy_number`, `insured_name`, `vin` differing across this project's documents | never auto-disputes |
| `review_summary` | counts of accepted / review / rejected / disputed and the top reasons | derived from `fields` |
| `quality_report.summary` | one calm sentence | e.g. "Low resolution on 1 of 1 page; signature is missing." |
| `laya` | `suggested_route`, `escalate`, `human_review`, `reasons` | rules over the flags |
| `replay` | `eligible`, `reasons`, `replayed: false`, `history` | intent only in V1 — nothing is re-parsed yet |

### A field

```json
{
  "name": "premium", "label": "Total premium", "field_type": "money",
  "value": 1284.0, "raw": "$1,284.00",
  "extraction_confidence": 0.85,
  "confidence_basis": "parser_default[jdf-cli] (0.85) × page_quality (1.00) = 0.85",
  "verification_confidence": 1.0,
  "provenance_confidence": 1.0,
  "source_span": {"page": 1, "span_type": "text_range", "start_char": 212, "end_char": 221},
  "number_quality": {"quality": "printed_good", "penalty": 1.0, "basis": "..."},
  "field_state": "unverified", "routing_action": "manual_review",
  "review_required": true, "reason": "compliance-bound field — human confirmation required",
  "compliance_bound": true, "z3_violation": false, "plausibility_violation": false
}
```

**`extraction_confidence`** is `parser confidence × page quality × number
penalty × Z3/plausibility penalty × signature penalty`. The basis string shows
the exact multiplication. `parser_confidence (0.93)` is a measured figure (the
OCR engine's own line confidence); `parser_default[...]` says no measurement
existed and the spec default was used. A field the extractor did not find is
`value: null, extraction_confidence: 0.0, review_required: true, reason:
"field not found"` — never a guess.

**`field_state`** is what we know: `accepted`, `partial`, `unverified`,
`disputed`, `rejected`. **`routing_action`** is what should happen: `none`,
`manual_review`, `adjudicator_queue`, `compliance_review`, `retry_parsure`,
`replay_later`. They never mix; "manual review" is never a state.

**Decision policy (v1, three rules).** Accepted when verification ≥ 0.8 and
extraction ≥ 0.75 and the field is not compliance-bound and nothing was
violated. Otherwise `unverified` + `manual_review`. A Z3 or plausibility
violation or an invalid VIN check digit → `rejected` + `compliance_review`.
Compliance-bound fields (policy number, VIN, premium, limits, signature)
therefore always ask for a human even on a perfect page — by design.

## Acting on a report

| Action | Route | Effect |
|---|---|---|
| Accept | `POST …/fields/<name>/accept` | `accepted` / `none`; audit event |
| Correct | `POST …/fields/<name>/correct {value, reason, actor}` | value replaced (VIN re-validated), `corrected: true`, correction row, replay becomes eligible |
| Dispute | `POST …/fields/<name>/dispute {reason, actor}` | `disputed` / `adjudicator_queue`; `due_at = opened_at + 72 h` |
| Resolve | `POST …/disputes/<id>/resolve {resolution, value?}` | `accepted` with a value, else `rejected` |
| Change type | `POST …/classification {document_type, reason}` | override recorded, fields re-extracted |
| Export | `GET …/export?format=json\|csv` | the report (JSON) or one row per field (CSV) |
| Export the project | `GET /api/projects/<id>/parsure/export?format=csv\|json[&wide=1][&document_type=…][&state=…]` | every document's fields in one file — see "Reading the data in bulk" below |
| History | `GET …/fields/<name>/history`, `GET …/parsure/audit-log` | every correction, dispute and event |

Audit events (`parsure_audit_events`, PostgreSQL): `intake_received`,
`quality_assessed`, `classified`, `fields_extracted`, `decision_applied`,
`field_accepted`, `field_corrected`, `dispute_opened`, `dispute_resolved`,
`classification_overridden`, `exported`.

## Measured behaviour (2026-09-25, compose stack, local models)

| Input | Page quality | Result |
|---|---|---|
| Crisp one-page auto policy PDF | 1.00 | 12 fields found; 7 accepted, 5 compliance-bound to review; signature line blank → "Signature is missing" |
| Same page as a 1224 px JPEG (phone-photo class) | 0.67 (`low_res` under the stated 11-inch assumption) | OCR 0.93 → confidence 0.62 → all fields to review |
| Same page at 368 px (unreadable) | 0.00 | type `uncertain`, no fields, Laya `human_review` |
| Golden set, 6 labelled synthetic documents | — | 100 % type and field accuracy (`scripts/validate_golden_set.py`) — synthetic, labelled; says nothing about real scans |

## What V1 does not do

Detect skew, glare, noise, handwriting, tables or mixed bundles from pixels;
learn anything (Laya is rules); re-parse on replay; extract from free prose
with the label rules alone; decode WebP.

## Reading the data in bulk (2026-09-26)

Client feedback of 2026-09-25: "How do we access the parsed information? …
Where can we show the parsed data in bulk?" The flow now reads upload → see
what came out → review → export, on three surfaces.

### `/parsing` — "Extracted data"

Right after "Needs attention" and before the document cards: one table per
document type present in the project, in taxonomy order (`auto_policy`,
`auto_claim`, …, `deed`, …), "Type uncertain" last. Rows are documents
(filename, date); columns are that type's fields in
`field_extractor.FIELD_TAXONOMY` order, labelled in words, plus any field a
report carries that the taxonomy does not name. A cell is the value with a
quiet mark: accepted in plain ink, needs review an amber dot, rejected or
disputed a red dot, not found "—". The cell's `title` carries the full value
and the reason. Money reads `1,284.00` (no currency sign — the extractor does
not record one), dates `Aug 14, 2026`, long text is cut at 40 characters.
Above the tables: a type select, a state select (all / needs review /
accepted / not found) and a value search, all over the rendered rows in plain
JS; a count line "12 documents · 144 values · 31 need review" from real counts
(values = fields with a value; need review = fields not accepted). The
section's leading action is **Export all** (CSV, one row per document);
"CSV, one row per field" and "JSON" are tertiary links. The export links
follow the type and state selects. Each row ends with **Review** (the Assure
shell, `/?project_id=…&report_id=…`) and **Record** (`/parsing/<report_id>`).
Empty: "No extracted data yet. Upload a document to begin."

### `GET /api/projects/<id>/parsure/export`

| Query | Result |
|---|---|
| `format=csv` (default) | **long**: one row per document × field — `report_id, document_id, filename, document_type, field, label, value, extraction_confidence, field_state, routing_action, review_required, reason, source_page, created_at`. Values raw (`1250.0`, `2026-08-14`), empty when not found. |
| `format=csv&wide=1` | **wide**: one row per document, one column per field headed by the field label, preceded by `report_id, filename, document_type, created_at, needs_review` (count of the row's fields still asking for a person). Columns are grouped by type in taxonomy order and deduplicated by field name; a label two fields share gets the name in parentheses. This is the spreadsheet shape. |
| `format=json` | `{ok, project_id, exported_at, filters, documents:[{report_id, document_id, filename, document_type, created_at, fields:{name: {…field…}}}]}` |
| `document_type=<type>` | only that type (`uncertain` for unnamed) |
| `state=needs_review\|accepted\|not_found` | only fields in that state; a document with no matching field is dropped. `needs_review` includes not-found fields (they also need a person); the two are "show me" filters, not a partition. Anything else → 400. |

`Content-Disposition: attachment; filename=parsure-<project>-<YYYY-MM-DD>.csv`.
One `exported` audit event per download with `payload.scope = "project"`,
the format, `wide`, the document and field counts and the filters;
`report_id` is null on that event. Private `_` keys are never exported.

### `GET /parsing/<report_id>` — the record

Server-rendered (`templates/parsure_detail.html`). The project comes from the
report (`parsure_repository.find_report`), `?project_id=` narrows it, and the
ownership check runs before rendering. Top to bottom: "← Parsure"; the
filename with type, source, modality and date in words and the quality
sentence; one primary action **Review in Assure**, tertiary Export JSON / CSV;
**Fields** — needs-review rows first — with value, state chip, confidence,
page, reason in words, the routing in words and a "Why this confidence"
disclosure showing `confidence_basis`; **Pages** — quality and flags in words
and the text of each page behind "Text of this page"; **Provenance & history**
— corrections (`original → corrected — reason`), disputes (with `due in 31 h`
/ `overdue by 5 h`), resolutions and the remaining audit events in one
timeline, newest first; **Technical details** collapsed (parser, version,
verification statuses, policy version, modality, material, type basis, Laya,
replay, ids). A missing report is a 404 page: "This record is not here."

The page text comes from `parsure_repository.report_page_texts(project_id,
report_id)`, which reads the report's private `_page_texts`. That helper is
used only by this page; `public_report` still strips the key from every API
response and export, and a report saved without texts reads "The text of this
page was not kept with the record." — never an empty page.

### Words

The pages and the exports share one vocabulary
(`routers/parsure_routes.py`, "Words" section): modality / material / flag /
state / routing / event names in words, `value_label` (money, dates,
numbers), `reason_words` (the policy's reason strings as sentences) and
`field_bucket` / `field_mark` (the filter a field answers to, the mark a cell
shows). A `value: null` field's confidence is the measured `0.0` and is shown
as such; a `None` confidence reads "—".

## Classification by evidence, `fields_found`, and one unit per count (2026-09-26)

Customer report on `real_estate_policy_500697.pdf`: every field read "not
found, confidence 0.00, unverified", and the page said "Need attention: 3"
above a section that said "62 items". Two findings, two fixes.

### The type was wrong, not the OCR

The keyword pass typed a real-estate declarations page as `auto_claim`
because its exclusions mention "claim", "date of loss" and "vehicle"; every
auto-claim field was then honestly empty. Classification now has three
stages, each recorded in `classification`:

| Stage | When | What it does | `classification.basis` |
|---|---|---|---|
| Keywords (`field_extractor.classify_document`) | always | counts each type's vocabulary; `uncertain` below 3 hits. **Near-tie rule:** when the top two types are within 1 hit (including an exact tie) the label pass runs for both and the type whose fields are actually found wins | `keyword heuristic: …` / `keyword near-tie (auto_claim 8, property_policy 7) decided by the label pass: property_policy 10/11 fields found vs auto_claim 1/10` |
| Evidence (`v1_orchestrator.reclassify_by_evidence`) | the keyword type finds ≤ 1 field **and** its confidence < 0.7 (or is `uncertain`) | runs the cheap label pass for every other type allowed by the family gate; switches to the one with the most found fields when it finds ≥ 3 (≥ 1 of them type-specific — see "Document families" below; was ≥ 2 of any field until 2026-09-26) and strictly more than the original. Regex only, deterministic | `reclassified by extraction evidence: property_policy 10/11 fields found vs auto_claim 1/10 (keywords said auto_claim, 8 hits)`; `confidence` = found ratio capped at 0.9; `method: extraction_evidence`; `detected` = the keyword answer |
| Model suggestion (`llm_extraction.classify_with_model`) | still `uncertain` after the two above **and** `PARSURE_LLM_EXTRACTION` is on | asks the configured model for exactly one of the ten type names over the first 3,000 characters; accepted only as an exact name **and** only if that type's fields are then found (≥ 1) | `model suggestion (<model>), confirmed by N fields found (N/M)`; `confidence` = found ratio capped at 0.6; `method: model_suggestion`; the model never supplies a value |

`TYPE_KEYWORDS["property_policy"]` also gained "real estate", "property
insurance", "dwelling coverage", "hazard insurance", "mortgagee". The type
that was tried is always in `classification.document_type`; the keyword
answer survives in `classification.detected` when they differ.
`reextract_for_type(..., by_evidence=True)` applies the evidence rule to a
requested type (a batch re-read); the reviewer's override route does not —
a reviewer's choice stands.

### "Nothing extracted" is one fact about the document

`review_summary.fields_found` is always present (also on `/parsure` list
summaries and as `queue.counts.fields_found`). When a typed document has
`fields_found == 0`:

- `quality_report.summary` starts with "No fields could be read as Auto
  claim." — or, when the whole upload carries fewer than 200 characters of
  text (`NO_TEXT_MIN_CHARS`), "No fields could be read as Auto claim — the
  pages carry 37 characters of text; the file may be a scan the OCR could not
  read." The count is the measured length of the page text; no OCR confidence
  is invented. `quality_report.text_chars` carries the number, `quality_flags`
  gets `no_text` at document level.
- `review_summary.reasons[0]` is "document type may be wrong — change it and
  the fields are re-read"; `replay.eligible` is true with the reason.
- `/parsing` card: amber "Nothing extracted — check the document type", facts
  "0 of 12 fields read", primary action **Check type** → the record page. An
  untyped report with no fields reads "Nothing extracted — choose the document
  type" / **Choose type** (it was "Ready for Assure" before).
- Review queue: the N empty rows of such a document fold into one row
  "`real_estate_policy_500697.pdf` — nothing extracted as Auto claim · Check
  type". Counts are unchanged by the fold (the header still says N fields).
- Record page: a notice above the Fields table with the sentence and an
  inline type selector (the ten types in words) that POSTs to
  `…/classification` and reloads; the page text is expanded by default so
  the reader sees what was read.

### One unit per count

`field_extractor.field_needs_review(field)` — routing not `none`, or state
`disputed` / `rejected` — is the single rule, and
`parsure_repository.attention_counts(reports)` computes `documents` (reports
with ≥ 1 such field or a conflict), `fields`, `nothing_extracted`,
`fields_found` once. It feeds `review_summary.fields_review` (hence the
`fields_review` column and `analytics.fields_review`), the queue and its
`counts` (`documents`, `nothing_extracted`, `fields_found` added), the
`/parsing` summary line ("Need attention: 3 documents · 62 fields"), the
"Needs attention" header ("62 fields across 3 documents"), the Extracted-data
count line ("12 documents · 144 values · 62 need review"), each card's
"N fields need review" and the record page's "Need review: N".
`tests/test_parsing_page.py::test_every_needs_attention_figure_is_the_same_number`
asserts the same 20 / 4 in every place for one seeded project.


## JDF node addressing (2026-09-26)

Every extracted field names where it came from, at three levels:

| Key | What | When |
|---|---|---|
| `source_span.page` + `bbox` / `text_range` | the page and the position on it | always when found |
| `field_source_node_id` | the JDF address the parse produced: on the import path the saved Assure paragraph id (`p-…`, the `data-node-id` the shell renders), on the Sources-pane path the jdf-cli chunk id (`p1e0` = page 1, element group 0) | always when found |
| `tree_node_id` (also `source_span.node_id`) | the saved Assure paragraph, resolved through the chunk id the tree keeps in `meta.chunk_id` or by direct match | when a saved tree exists (import path); `null` on a Sources-pane upload, which has no tree |

The record page shows the node id under the page number; the shell's Fields
panel jumps to the paragraph when the id is on screen (`Page 1` → the node).
Until 2026-09-26 `field_source_node_id` was `null` on every field because
jdf-cli 0.2.3 elements carry no `id`; the chunk id is the address that
survives from parse to tree to review.

## Export — the Verification Dossier (2026-09-26)

Customer QA on an exported PDF: it was titled "Formal Verification
Certificate" while the run's JSON read `laya.suggested_route = human_review`,
`fields_accepted 0 / fields_review 12`, a cross-document conflict on
`insured_name` and `signature_quality.quality = questionable`; the PDF said
"No locked claims recorded", "No cross-run contradictions detected", "No
Red-Hat findings recorded"; and the file itself was the text fallback of
`exporters/pdf_ast.py`, announcing the missing engines on page 1. Three
rules now hold, all in `services/verification_dossier.py`.

### One trust state, derived — never declared

`derive_trust_state` maps the record to one of three states; the title, the
subtitle and the zip manifest all read it. The word **Certificate** is
emitted only in the `verified` state.

| State | Title | Rule (checked in this order) |
|---|---|---|
| `not_verified` | Verification Dossier — Not verified | gate `blocked`, or Z3 `VIOLATION`, or a claim contradicted by its source (`provenance_stats.unsupported > 0`), or **nothing accepted at all** (0 locked claims + 0 supported claims + 0 accepted intake fields) |
| `review_required` | Verification Dossier — Review required | otherwise, when anything is open: gate not `pass`, gate `unverified`, ≥ 1 field needs review (`field_extractor.field_needs_review`), ≥ 1 open dispute, ≥ 1 conflict (cross-document or cross-run), ≥ 1 open Red-Hat finding, or the signature is unresolved (`questionable` / `faint` / `incomplete` / missing) |
| `verified` | Verification Dossier — Verified | none of the above |

Inputs: `audit_bundle.compute_export_gate` (gate, Z3, provenance counts), the
lock ledger (runs + draft `meta.lock_ledger`), `parsure_repository.list_reports`
(current reports — review counts recounted with the queue's rule, `conflicts[]`,
`quality_report.signature`, pages/flags/`no_text`, `replay`, `laya`),
`audit_bundle.project_redhat_findings` (ran / count / items),
`parsure_repository.list_disputes(status="open")`, `macro_verify.
detect_cross_run_contradictions` (only with ≥ 2 runs).

### The status band

Under the title, real numbers or "not run" — never "No … detected" for a check
that did not run:

```
12 fields need review · 0 accepted · 1 conflict · signature questionable · Red-Hat: not run · gate review
0 fields need review · 4 accepted · 0 conflicts · signature stamped · Red-Hat: 0 findings · gate pass
no intake report · conflicts: not run · signature not assessed · Red-Hat: not run · gate review
```

Open disputes are appended when there are any (`1 open dispute (1 overdue)`).

### Sections (each present, with the reason when empty)

1. Summary — gate, Z3, claims supported / contradicted / anchored, locked
   claims, intake field counts, conflicts, Red-Hat, disputes, documents; Laya
   triage table (`suggested_route`, `human_review`, `escalate`, reasons).
2. Review required — every field asking for a person: document, field, value
   read, confidence (%), state / routing, reason, page. Empty: "0 fields need
   review." or "Not run — no intake report".
3. Conflicts — cross-document (`field`, each value with its document, and why
   it is a contradiction), then cross-run contradictions ("Not run — fewer
   than two runs recorded (N)" when they were not compared).
4. Red-Hat findings — "Red-Hat: N findings, M open" with the table, or "Red-Hat
   ran and recorded 0 findings", or "Not run — <the compile's skip reason>".
5. Locked claims — the ledger as before; "0 locked claims — no claim has been
   locked for this project" when empty.
6. Signature — one row per document: `present but questionable — <basis>`,
   `present but faint`, `missing`, `confirmed (stamp or electronic signature)`,
   `not assessed`; unresolved rows are flagged and block `verified`. The basis
   is `quality_probe.assess_signature`'s measurement, verbatim.
7. Disputes — open ones with reason, opened, `due in 31 h` / `overdue by 5 h`.
8. Quality — per document: score, flags (`no_text` flagged), characters of
   text, parser; per page: quality, flags, OCR confidence or "not measured".
9. Replay eligibility — per document, with the reasons; "Replayed: no (V1
   records eligibility only)".
10. Document — the body (latest JDF revision, else the founder draft).

### The machine-readable twin

`GET /api/projects/<id>/export?format=bundle` → `<project>-<version>-dossier.zip`:

| Member | What |
|---|---|
| `<stem>.pdf` | the Verification Dossier |
| `<stem>-audit-report.pdf` | the Compliance Audit Report (unchanged) |
| `<stem>.jdf.json` | the JDF sidecar (unchanged) |
| `verification_state.json` | the dict the dossier HTML was rendered from — `trust_state`, `title`, `status_band`, `reasons`, `counts`, `gate`, `intake`, `sections` (schema `assure.verification_state/1`). `build_dossier` builds it once and renders the HTML from it, so PDF and JSON cannot disagree |
| `manifest.json` | every member with size and SHA-256, the trust state, `pdf.included` and — when no renderer is present — `pdf.reason` and each engine's probe result |

`format=dossier-pdf` also returns `X-Assure-Trust-State` and
`X-Assure-Renderer` headers.

### Renderer: real or refused

`render_pdf` uses Playwright when a Chromium actually launches (probe cached
per process), else WeasyPrint, else raises `PdfRendererUnavailable`. Every
PDF format (`pdf`, `audit-pdf`, `dossier-pdf`, `pdf&audit_bundle=1`) then
answers **503** `{"ok": false, "error": "PDF renderer unavailable on this
server", "detail": {"playwright": …, "weasyprint": …}}`; `bundle` still
answers 200 without the PDF members and says so in the manifest. The text
writer in `exporters/pdf_ast.py` is no longer reachable from a route.

The image installs WeasyPrint (`requirements.txt`) and its native stack in the
runtime stage (`libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0
libffi8 shared-mime-info fonts-dejavu-core`). Check:
`docker compose run --rm assure-app python -c "import weasyprint; print(weasyprint.__version__)"`.

Measured 2026-09-26 on the compose stack, project `node-check-4` (one saved
document, one intake report), before and after this change:

| | Old image (`exporters/pdf_ast.py` fallback) | New image |
|---|---|---|
| `dossier-pdf` | 200, 1 page, fonts `{Helvetica}` (unembedded core font), text begins "This dossier was rendered as text…", titled "Formal Verification Certificate" | 200, 4 pages, producer `WeasyPrint 70.0`, embedded `DejaVu-Serif`, `DejaVu-Serif-Bold`, `DejaVu-Serif-Oblique`, `DejaVu-Sans-Mono`; no "fallback" / "rendered as text"; title "Verification Dossier — Review required"; band "5 fields need review · 7 accepted · 0 conflicts · signature missing · Red-Hat: not run · gate review"; headers `X-Assure-Trust-State: review_required`, `X-Assure-Renderer: weasyprint` |
| `bundle` | 2 members | 5 members; `manifest.pdf = {"included": true, "renderer": "weasyprint"}`; `manifest.trust_state == verification_state.trust_state` |
| in-image probe | — | `renderer_status()` → `playwright: browser not installed: … Executable doesn't exist …`, `weasyprint: ok (70.0)` |

## Document families, evidence states, graph integrity, field-level confidence (2026-09-26)

Customer QA on a CMS-1500 medical claim (page quality 0.28): the report came
back `classification.document_type = auto_policy` with
`detected.document_type = uncertain` (the only keyword hit was "accident"),
the auto-policy taxonomy with `fields_accepted 0 / fields_review 12`,
`field_source_node_id` on three found fields and `null` on the rest. Four
fixes, all in `field_extractor` / `v1_orchestrator`; the record page,
cards and queue read them.

### Document family gate — before any schema is assigned

| Piece | What |
|---|---|
| `medical_claim` type | CMS-1500 (NUCC 02/12) taxonomy: `patient_name`, `insured_id`, `patient_dob`, `insured_name`, `diagnosis_codes` (ICD, a `codes` list), `procedure_codes` (CPT/HCPCS, list), `date_of_service`, `provider_name`, `provider_npi`, `federal_tax_id`, `total_charge`, `amount_paid`, `signature`. Keywords: cms-1500, health insurance claim form, patient, insured's id / i.d. number, diagnosis, icd, cpt, npi, place of service, total charge, amount paid, rendering provider, medicare, medicaid, group health plan. Golden fixture `tests/golden/medical_claim_1.txt` (12/12 fields; the set stays 100 %, now 64 fields). |
| `field_extractor.document_family(text)` | `{"family": auto \| property \| real_estate_transaction \| medical \| unknown, "cues": [...], "counts": {...}, "basis"}` from **strong cues** (`FAMILY_CUES`): auto = vin / vehicle / collision / automobile / odometer / lienholder; property = dwelling / coverage a / homeowners / hazard insurance / personal property / insured location; transaction = deed / grantor / grantee / mortgagee / borrower / lender / promissory note / closing disclosure / settlement statement / title commitment / legal description / parcel / escrow; medical = the CMS-1500 keywords. A family is named at ≥ 2 cues **and** a margin of ≥ 2 over the runner-up; otherwise `unknown` and the basis lists the competing counts (the lender-facing declarations page whose exclusions mention vehicle/VIN/collision scores auto 3 / property 2 → unknown → the label pass decides as before). "accident", "claim", "premium", "deductible" are on every form and are **not** cues. |
| The gate (`field_extractor.type_allowed`) | A type may be chosen — by keywords, by `reclassify_by_evidence`, by the model suggestion, by the family fallback — only when `TYPE_FAMILY[type]` equals the detected family or the family is `unknown`. `classify_document` runs only the allowed types and returns `family`; its basis carries `family gate: medical (…)`. |
| Evidence rule, tightened | `reclassify_by_evidence` switches only to a candidate that finds **≥ 3 fields** (`RECLASSIFY_MIN_FOUND`) **of which ≥ 1 is type-specific** — not in `SHARED_FIELD_NAMES = {policy_number, insured_name, signature, effective_date, expiration_date}`. The customer's page was won by exactly those shared fields. `evidence.type_specific` / `type_specific_fields` record the proof. The model suggestion obeys the same gate and is refused when only shared fields confirm it (`extraction_notes`: "model suggested auto_policy but only shared fields were found (…)"). |
| Family fallback (`v1_orchestrator.family_fallback`) | When the cues name a family and the keyword answer is weak (`uncertain` or confidence < 0.7): the family's schema with the most type-specific fields is chosen when it finds ≥ 2 of them (`method: family_evidence`); otherwise `classification.document_type = "<family>_unknown"` (e.g. `medical_unknown`) with **no taxonomy fields**, `classification.schema_mismatch = true`, `classification.suggestion = {document_type, found, total, type_specific, …}` (the nearest schema), and a basis that says why no schema was forced. A confident keyword type (≥ 0.7) is never second-guessed here — "nothing extracted as Auto claim — check the type" stays the path for a well-named type whose labels the OCR did not read. |
| Validation (`classification.validation`) | `{"family", "type_family", "agrees", "cues", "basis", "type_specific", "type_specific_fields"}` on every report, computed after the final type. A reviewer's override to a type of another family stands, but when `agrees` is false **and** that type finds < 2 type-specific fields the report gets `schema_mismatch = true` and its fields are marked (below). `classification.family` keeps the cue record; the `classified` audit event carries `validation`, `schema_mismatch`, `suggestion`. |

Tests pinning the case: `tests/test_field_extractor.py::test_cms_1500_with_the_word_accident_is_a_medical_claim_with_its_fields`
(synthetic CMS-1500 with "Auto Accident?" → `medical_claim`, ≥ 6 found) and
`::test_cms_1500_without_medical_cues_is_never_auto_policy` (same page with
the medical cues removed and the shared fields present → `uncertain`);
`tests/test_v1_orchestrator.py::test_cms_1500_without_medical_cues_stays_uncertain_never_auto_policy`
(through `run_after_parse`, with a model that says `auto_policy` and is
refused) and `::test_family_cues_without_a_fitting_schema_are_family_unknown_not_a_forced_schema`.

### Every field carries an anchor (`graph_integrity`)

A found field names the node its value sits on (`field_source_node_id`,
`tree_node_id`, `source_span`), as before. A field that was **not** found
now carries

```
evidence = {"kind": "absent", "searched_pages": [1..N], "searched_node_ids": [first 50 layout node ids],
            "searched_chars": N, "anchor_node_id": <first layout node id>, "anchor_kind": "layout_node" | "document_root",
            "readability": ["readable" | "low" | "unreadable", …]}
source_span = {"span_type": "absent", "pages": [...], "node_id": <tree node when a tree exists>}
field_source_node_id = anchor_node_id
```

`attach_tree_node_ids` maps the anchor through `meta.chunk_id` like any
found field, and hangs an absent field with no layout anchor off the tree's
first node (`anchor_kind: document_root`) — never an invented id. Found
fields carry `evidence = {"kind": "found", "page", "node_id", "method"}`.
The report's `graph_integrity = {"fields", "anchored", "orphans", "absent_anchored", "checked_at", "basis"}`
is computed after the tree ids are attached (and again by `refresh_report`);
`orphans` is 0 whenever the parse produced any node id, and the basis says
so when a flat-text upload produced none. `attach_z3_violations` skips
absent fields so a violation on the anchor node is not attributed to them.
The record page prints the anchor in the node column with the word
"searched" (`el-0 · searched`) and the searched page range in the page
column.

### `evidence_state` — the five outcomes, apart from `field_state`

| `evidence_state` | When | `reason` (plain words) |
|---|---|---|
| `found_verified` | value, policy accepted | — |
| `found_unverified` | value, policy did not accept (unverified / rejected / disputed) | the policy's reason |
| `not_on_document` | no value, and at least one searched page is *readable* (≥ 200 characters and page quality ≥ 0.5, or quality unknown) | "Not on this document type" |
| `unreadable` | no value, and every searched page is *unreadable* (< 200 characters, or page quality < 0.3) | "Page could not be read" |
| `schema_mismatch` | the type's family disagrees with the page and its fields are not there (override case) | "Wrong document type — fields not applicable" |

Pages between the two thresholds (quality 0.3–0.5) still read
`not_on_document`, with "page quality low on p.N" in the confidence basis.
`field_extractor.page_readability` is the rule; `attach_absent_evidence`
applies it; `apply_decision_policy` sets the two `found_*` states and leaves
absent and mismatch states alone. Schema-mismatch fields have
`extraction_confidence: null`, `confidence_basis: "not computed: schema
mismatch"`, routing `none`, and **do not count as needing review**
(`field_needs_review` returns false); the document counts once —
`attention_counts` gained `schema_mismatch`, `list_queue` emits one item of
`kind: "schema_mismatch"` per such report (not in `total`, which stays the
field count), `review_summary` gained `evidence_states` (a histogram) and
`schema_mismatch`, and its first reason is "wrong document type — the
fields of this type are not on the page".

Surfaces: the card shows one amber line "Wrong document type — read as
Medical claim?" (`Check type` → record); the queue shows one row of the
same words; the record page's notice carries the line and the type
selector pre-set to the suggestion, the Fields header reads "13 not
applicable — wrong document type", and each row shows the evidence words
under the state chip (`EVIDENCE_WORDS`: "Found, verified", "Found, needs a
look", "Not on this document", "Page unreadable", "Wrong document type").
`<family>_unknown` reads "Medical — type unknown".

### Field-level confidence

`page_layout` segments now carry `ocr_confidence` (mean of the jdf-cli
`ocr.blocks[].confidence` inside that element; `None` on the text layer)
and `local_quality` (= that OCR confidence when present). `build_found_field`
uses the segment's local quality in place of the page score when it exists
— the spec §4 product is unchanged, the factor is the nearer measurement —
and the basis names it: `parser_default[jdf-cli] (0.85) × local_ocr (0.91) =
0.77` versus `… × page_quality (0.28) = 0.24` for a field on a line the
OCR did not measure. The field records `quality_source`
(`local_ocr` | `page_quality`) and `local_quality`; `assess_number` receives
the local OCR confidence for that field's digits.
`quality_weighted_confidence(..., quality_label=…)` (both `quality_probe` and
the extractor fallback) is where the label enters the basis.

## Stable element identity and evidence granularity (2026-09-26)

Two P0s from the customer's benchmark plan — "evidence granularity is too
coarse" and "missing stable element IDs" — had one root: jdf-cli 0.2.3
`pages[].elements[]` carry no `id`, `jdf chunk --strategy section` (the
ingest default) folds every element of a page into **one** chunk, and the
tree paragraph id is `new_node_id("p")` (random per run). Measured
2026-09-26 on `/tmp/auto_policy.pdf` and the `tests/golden/prose` fixtures
rendered with PyMuPDF:

| Document | pages | jdf elements | `section` chunks | `element` chunks | `fixed` chunks |
|---|---|---|---|---|---|
| auto_policy.pdf | 1 | 6 | 1 | 6 | 1 |
| auto_claim_1 / mortgage_1 / property_policy_1 | 1 | 5 | 1 | 5 | 1 |
| auto_policy_1 / auto_policy_2 | 1 | 6 | 1 | 6 | 1 |
| deed_1 | 1 | 7 | 1 | 7 | 1 |
| mixed_bundle_1 | 3 | 16 | **1** (`page: 1`) | 16 (`p1e0…p3e6`) | 1 |

So under `section` every found field of a one-page declarations PDF pointed
at the same paragraph, and a three-page bundle became one paragraph on
"Page 1" with two empty pages. `element` gives one chunk per jdf element
with correct page attribution and ids `p<page>e<index>`. The ingest default
stays `section` for now — `pdf_ingest.py` and `jdf_memory_routes.py` pass
`strategy="section"` explicitly; that pin was removed the same day and `jdf_converter.CHUNK_STRATEGY_DEFAULT` is now `element` (`JDF_CHUNK_STRATEGY=section` restores the old shape). `meta.elements` stays on every paragraph, so a paragraph is still addressable below its own boundary.

### `meta.elements` on every chunk paragraph

`jdf_converter.chunk_elements` lists, for each chunk paragraph, the jdf-cli
elements whose text the chunk contains (matched in document order with a
moving cursor, exact first then whitespace-tolerant), with offsets into the
paragraph's `content`:

```
meta = {"chunk_id": "p1e0", "source_page": 1,
        "elements": [{"element_id": "p1e0:fcb36afc0079", "page": 1,
                      "bbox": [0.0981, 0.0902, 0.5571, 0.1069],
                      "start_char": 0, "end_char": 90,
                      "text_preview": "AUTO INSURANCE POLICY DECLARATIONS\nPolic"},
                     {"element_id": "p1e0:3adfe5047438", "page": 1, "bbox": [...],
                      "start_char": 92, "end_char": 176, "text_preview": "Effective Date: 03/15/2026 Expiration Da"},
                     …]}
```

The paragraph itself is unchanged (one per chunk, `content`, random `id`);
the tree's `meta.node_id_policy` reads `eid-v1`. `page_layout` copies the
list onto the paragraph's layout segment (`seg["elements"]`) on the import
path, where `bundle["jdf"]` is the saved tree, so `build_found_field`
resolves a value to the element whose range holds it: `source_span` gets
that element's `bbox` (`span_type: bbox_relative` even though the tree
segment has none), `element_id`, and `node_offsets = {start_char, end_char}`
inside the paragraph. On the Sources-pane path (raw jdf-cli pages) the
segment *is* the element, and `attach_tree_node_ids` adds `node_offsets`
by finding the field's `raw` text inside the named element's range of the
paragraph. `models.jdf.JDFElementRef` states the entry shape.

### Policy `eid-v1`

`field_extractor.derive_element_id(chunk_id, page, bbox, text)`:

```
f"{chunk_id or 'p'+page}:{sha1(bbox rounded to 3 dp joined by ',' + '|' + whitespace-collapsed text)[:12]}"
```

- Deterministic across runs and processes: no uuid, no clock, no counter.
  `tests/test_node_identity.py` parses the same bundle twice, regenerates
  the tree (new paragraph ids) and derives the id in a fresh interpreter —
  identical ids every time; a changed word or a moved box is a new id,
  sub-0.001 jitter or re-wrapped whitespace is not.
- Where it appears: every layout segment (`element_id`, plus `chunk_id`),
  every found field (`element_id`, `source_span.element_id`,
  `evidence.element_id`), every `meta.elements` entry, and the report:
  `node_id_policy: "eid-v1"`, `identity: {"policy": "eid-v1", "derivation":
  "chunk_id + bbox(3dp) + text sha1[:12]"}`, `graph_integrity.element_ids`
  (found fields naming an element) and `graph_integrity.policy`.
- Absent fields carry `element_id: null` — the anchor they were searched
  from is a node, not an element, and nothing is invented.
- **Versioning rule:** any change to the derivation (hash, rounding,
  prefix, what goes into the text) bumps `NODE_ID_POLICY` (`eid-v2`, …),
  is recorded in this section with the date and the reason, and ids of
  different policies are never compared. `models.jdf.KNOWN_NODE_ID_POLICIES`
  lists the versions the model layer knows.

### Per-stage timings and latency class

`report["timings_ms"] = {"layout", "classify", "extract", "llm_extract",
"quality", "total"}` — wall time (`perf_counter`, ms) of `page_texts` +
`page_layout`; `settle_segment_type`; `extract_segment_fields` *minus* the
model call; the grounded LLM pass (`lx.extract_missing_fields`, timed
through a context variable so `llm_fill_missing` needs no extra argument);
`score_pages` + `compose_quality_summary`; and the whole of `build_report`.
A stage that did not run reads `0.0`. `report["latency_class"]` is one of
`policy_form | claim_packet | photo_signature | mixed_bundle`
(`v1_orchestrator.latency_class`): several documents or
`material_type = mixed_bundle` → `mixed_bundle`; material `photo` / `image`
/ `screenshot` or modality `phone_photo` / `screenshot` → `photo_signature`
(a scanned *PDF* is a `policy_form`/`claim_packet` — same page shape, its
cost is the OCR named by `parser_name`); `medical_claim` / `auto_claim` /
`property_claim` → `claim_packet`; else `policy_form`. Analytics may read
these; the report only records them.

## Automated Red-Hat — the intake graph critique (2026-09-26)

The customer's benchmark plan makes Red-Hat a first-class **automatic
adversarial critique over the structured evidence graph** (P0). That pass
is `services/redhat_graph.py`, policy `rh-graph-v1`. It is a *second*
Red-Hat, apart from `tasks/redhat.py` (the multipass audit that argues with
a compiled draft): this one reads the intake report — classification and
its family validation, every field's anchor (`field_source_node_id`,
`tree_node_id`, `source_span`), evidence state, the three confidences and
their basis, page quality / flags / OCR confidence, `conflicts[]`,
`graph_integrity`, the corrections, disputes and events against the report,
and the export state when an export is being judged — and returns findings.
It never re-parses, never names a value, and never runs on the web tier
(it runs where `run_after_parse` runs).

```
critique_report(report, *, tree=None, corrections=None, disputes=None, events=None,
                export_state=None, completion=None, llm=None, timeout_s=45) -> {
    "policy": "rh-graph-v1", "ran_at", "findings": [...], "counts": {"high","medium","low"},
    "classes": {"structural","evidentiary","export"}, "notes": [...], "rules": [...]}
attach_findings(report, findings_or_block, *, notes=None) -> report["redhat"]
```

`attach_findings` writes `report["redhat"]` and prepends each high finding
to `review_summary.reasons` as `Red-Hat: <title>`, so the card, the queue
and the record read it without knowing the module.

### A finding

```
{"id": "rh-evidentiary-1", "rule": "cross_document_conflict", "policy": "rh-graph-v1",
 "title": "Insured name conflicts with another document", "severity": "high",
 "class": "evidentiary",
 "anchor": {"node_id": "p-ffa2…", "element_id": "p1e4", "page": 1, "field": "insured_name", "kind": "field"},
 "rationale": "insured name differs across 2 documents ('Jane Q. Public', 'Jane Public-Smith'); a dispute is open on it."}
```

The anchor is never empty. A field finding names the field's node and page
(`kind: field`); a page finding the page's first node (`kind: page`); a
document finding the root — the tree's first body node, else the document
id — with `kind: document_root` and a `note` saying no closer node exists.
Surfaces print it as `Field insured name · Page 1 · p1e4`, `Page 2 · p2e0`,
or `Document root · doc-…`. Findings have no open/closed state in V1 (there
is no dismiss action); a high finding stays a review item until the report
is re-read.

### Rules (deterministic; each states the signal it reads)

| Rule | Signal | Severity · class |
|---|---|---|
| `wrong_document_family` | `classification.validation.agrees` false; `schema_mismatch` | high (family contradicts the type) / medium (no schema of the family fits) · structural |
| `coarse_chunking` | ≥ 2 found fields on a page share one node while the page has > 1 layout element or > 1,500 characters | medium · structural |
| `unstable_or_missing_ids` | found field without `field_source_node_id`; `graph_integrity.orphans > 0`; no `node_id_policy` on the report | medium / medium / low · structural |
| `broken_connectivity` | with a tree in hand: found field whose `tree_node_id` the tree lacks; found field with `tree_node_id` null | high / medium · structural (not run without a tree — noted) |
| `not_applicable_vs_not_found` | `not_on_document` while the family disagrees; `unreadable` with a "not found" reason; absent field with no `evidence_state` | medium / medium / low · evidentiary |
| `signature_ambiguity` | `signature_quality` questionable / faint / incomplete / stamped; `missing` where the schema has a signature field | medium (low once a reviewer accepted it) / low · evidentiary |
| `low_quality_evidence` | page quality < 0.5, flags low_res / blurry / low_contrast / no_text, OCR < 0.7 on a page with found fields | medium when a found field rests on the page, low otherwise · evidentiary |
| `cross_document_conflict` | each `conflicts[]` entry (open dispute / correction on the field named in the rationale) | high · evidentiary |
| `export_overclaiming` | `export_state.trust_state != verified` with certificate language in title / subtitle / language | high · export |
| `confidence_flattening` | extraction = verification = provenance confidence with a "default" basis; ≥ 80 % of ≥ 3 found fields share one extraction confidence | medium / low · evidentiary |
| `fallback_rendering` | `export_state.renderer` in text / fallback (or `fallback: true`) | high · export |

Export rules read `export_state` (the dossier's `build_verification_state`
output or the exporter's record); without one they do not run and the
notes say so.

### Model-assisted unsupported-claim check (grounded, optional)

`unsupported_claim` asks the `REDHAT` policy model (the backend
`cost_governance` resolves — Ollama locally, Opus on Bedrock, the
OpenRouter policy) over the found fields and the page text they anchor to
which values the text does **not** support. A candidate is kept only when
its `quote` is found verbatim in the page text (`llm_extraction.find_verbatim`,
whitespace-collapsed, case-insensitive) and names a found field; the rest
are dropped and counted in the note. Findings are medium, evidentiary, and
carry `quote` and `model`. Bounded at 45 s (`LLM_TIMEOUT_S`), off with
`PARSURE_REDHAT_LLM=0`, skipped with a reason (no backend, no page text, no
found field, timeout, unparsable answer) in `report["redhat"]["notes"]`.
The model never adds a value and never removes one — it can only point at
a line.

### Where the findings show

| Surface | What |
|---|---|
| `/parsing/<report_id>` | "Red-Hat findings" section: severity chip, title, class, anchor, one-sentence rationale; "Not run — no critique is recorded for this report." vs "No findings." (only when a critique ran); "What did not run" folds the notes |
| `/parsing` card | one line "N Red-Hat findings (M high)" linking to the record's section — red only for a high finding, amber for medium, quiet for low; nothing when not run |
| `GET …/parsure/queue` | `counts.redhat_high` (the header says "N Red-Hat high") |
| `GET …/parsure/analytics` | `redhat_reports_run`, `redhat_findings_by_class`, `redhat_findings_by_severity` — zeros next to `redhat_reports_run: 0` read "not run" |
| `GET …/parsure` list | each summary carries `redhat: {ran, count, high, medium, low, classes}` |
| Verification Dossier §4 | two blocks, labelled **draft audit** (`tasks/redhat.py`, read by `audit_bundle.project_redhat_findings`) and **intake graph critique** (`report["redhat"]` over the project's intake reports); `counts.redhat_intake_findings` / `redhat_intake_high`; the status band says `intake critique: N findings (M high)` or `intake critique: not run` |

`derive_trust_state(..., intake_redhat_high=N)`: a high intake finding is
`review_required` ("N high Red-Hat finding(s) on the intake graph"), never
`verified`.
