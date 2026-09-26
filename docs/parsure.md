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
| Evidence (`v1_orchestrator.reclassify_by_evidence`) | the keyword type finds ≤ 1 field **and** its confidence < 0.7 (or is `uncertain`) | runs the cheap label pass for every other type; switches to the one with the most found fields when it finds ≥ 2 and strictly more than the original. Regex only, deterministic | `reclassified by extraction evidence: property_policy 10/11 fields found vs auto_claim 1/10 (keywords said auto_claim, 8 hits)`; `confidence` = found ratio capped at 0.9; `method: extraction_evidence`; `detected` = the keyword answer |
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
