# Parsure corpus taxonomy — what each document family gets today

The customer's benchmark plan (§3 corpus coverage, §7 P1 "not enough
document-family coverage") asks for an explicit taxonomy: for every family,
which parser path it takes, which schema reads it, what the fallback is,
which latency class it belongs to, and what is missing. This page is that
table, written from the code as it stands on 2026-09-26 (commit `8ad8481`
plus the identity / Red-Hat-graph / timings work landing alongside). Every
"today" statement below names the function that makes it true. The frozen
benchmark set that exercises these families is `bench/manifest.json`
(`docs/benchmark.md`).

## The machinery the table refers to

**Router — `services/parser_router.select_parser` (one place, guard-tested).**

| Input | Decision | Then |
|---|---|---|
| extension in `txt md json csv rst yaml yml`, or `source_kind="text"` | `jdf` | the caller wraps the text as a JDF document; no binary parse |
| extension in `png jpg jpeg tif tiff bmp` | `scan_backend()` | `pdf_ingest` wraps the image in a one-page PDF for jdf-cli (BMP → PNG for Textract) |
| `.pdf` | probe the first 3 pages for a text layer (`_probe_pdf_for_parser`) | text → `jdf`; none → `scan_backend()`; probe error → `jdf` |
| `source_kind="scanned"` | `scan_backend()` | client says the file is image-only |
| anything else | `jdf` | |

`scan_backend()` is `jdf-ocr` (jdf-cli 0.2.3 with bundled tesseract.js, free,
~1 s/page measured locally) unless `PARSER_SCAN_BACKEND=textract` or
`JDF_OCR=none`, in which case it is Amazon Textract (per-page fee). Routing is
by *container*, never by content: the router does not know a policy from a
photo of a policy.

**Execution and fallbacks — `services/pdf_ingest.ingest_pdf_for_project`.**

1. `jdf-ocr` raises `JdfConversionError` → Textract.
2. `jdf-ocr` returns no text → Textract; Textract unavailable → `PdfIngestError`
   400 "Could not extract enough readable text from this file (0 characters
   after OCR)". The upload fails; no revision is saved.
3. Any other failure on the jdf path → PyMuPDF text import (`parser_name=
   "pymupdf"`, `parse_confidence=None`, `ocr_confidence=None`).
4. Verification (Z3 + the Red-Hat shape pass) never raises; the report may
   read `z3_status: ERROR`.

**Intake annotation — `route_intake`** adds `material_type` / `modality`
(`quality_probe.detect_material`: `pdf/digital_pdf`, `pdf/scanned_pdf`,
`image/scanned_pdf`, `photo/phone_photo` by dimensions and EXIF,
`screenshot/screenshot`, `text_file/text`; `handwritten_image` and `table`
only from a client hint), the per-page visual probe (`low_res`, `blurry`,
`low_contrast`, `no_text` — skew, glare, noise and handwriting are **not**
detected) and Laya rules-v1 (≥ 50 % flagged pages → `human_review`). None of
it changes the parser.

**Classification — `services/field_extractor` + `services/v1_orchestrator`.**
`DOCUMENT_TYPES` = `auto_policy, auto_claim, auto_title, property_policy,
property_claim, deed, mortgage, title, closing, medical_claim`; the report may
also say `uncertain`, `mixed_bundle`, or `<family>_unknown` for the four
families `auto, property, real_estate_transaction, medical`. Keyword pass
(≥ 3 hits) → family gate (`document_family`, strong cues) → near-tie broken
by the label pass → `reclassify_by_evidence` (≥ 3 fields, ≥ 1 type-specific)
→ model suggestion (LLM on) → `family_fallback` (`<family>_unknown` with
`schema_mismatch: true` when the family is known but no schema fits). Mixed
bundles are split by **page-level classification change only**
(`segment_pages`); there is no visual boundary detection.

**Signature — `quality_probe.assess_signature`.** A label in the text
("Signature", "Signed", "/s/") locates the expectation; the ink and mark
ratios of the **bottom 20 % of the page** decide presence. Stamp / e-signed
wording next to the label → `stamped`. It never answers `clear`. A signature
block that sits above the bottom fifth of the page is judged by the wrong
pixels — the benchmark's declarations page, whose `/s/` line ends at 45 %
of the page height, reads `present: false` (measured 2026-09-26).

**Latency class — `v1_orchestrator.latency_class`** (derived, on every
report): several documents → `mixed_bundle`; material `photo/image/
screenshot` or modality `phone_photo/screenshot` → `photo_signature`; a claim
schema → `claim_packet`; else `policy_form`. Note the code's rule puts a
*scanned PDF* in `policy_form`/`claim_packet` by type (its cost is OCR,
tracked by `parser_name`), while the benchmark manifest groups scanned
attachments and signature/handwriting scans under `photo_signature` as the
plan does; the harness reports the agreement rate (54 % on `bench-v1`).

## The families

Latency ceilings (P95, plan §8): policy_form < 5 s, claim_packet < 8 s,
photo_signature < 10 s, mixed_bundle < 15 s. "Router path today" assumes
the default `PARSER_SCAN_BACKEND` (jdf-ocr). "Schema today" names the
`DOCUMENT_TYPES` entry that reads the family or says none exists.

### Policy & underwriting

| Family | Modality (typical) | Router path today | Schema today | Fallback behaviour | Latency class | What is missing |
|---|---|---|---|---|---|---|
| Policy packet (full policy with forms) | digital PDF, 10–60 pp | `jdf`; pages without text inside a digital PDF are not OCR'd (probe is first 3 pages only) | `auto_policy` / `property_policy` from the declarations vocabulary; the policy forms themselves carry no fields | keyword pass on the joined text; long packets dilute cues → `uncertain` or `<family>_unknown` | policy_form | a packet-level segmenter (declarations vs forms vs endorsements are not split unless the keyword type changes); form-number extraction (`HO 00 03 05 11`) |
| Declarations page | digital PDF or scan | `jdf` / `jdf-ocr` | `auto_policy` (12 fields), `property_policy` (12 fields) | evidence reclassification, family fallback | policy_form | `auto_title` exists but has no benchmark case; commercial lines (GL, BOP, WC) have no type at all |
| Endorsement | digital PDF, 1–3 pp | `jdf` | **none** — reads as `auto_policy`/`property_policy` (bench: `auto_policy`, 5/8 fields; the *removed* VIN is extracted, the added one is not) | no endorsement-aware pass; premium delta read as premium | policy_form | `endorsement` type with `endorsement_number`, `change_effective_date`, added/removed vehicle or coverage, `premium_change` |
| Cancellation / reinstatement | digital PDF or mailed scan | `jdf` / `jdf-ocr` | **none** — reads as `auto_policy` (bench: 5/5 of the shared fields) | none | policy_form | `cancellation_notice` type: `cancellation_effective_date`, `reason`, `amount_due`, `reinstatement_terms` |
| Renewal | digital PDF | `jdf` | `auto_policy` / `property_policy` (a renewal declarations page is a declarations page) | as declarations | policy_form | a renewal ↔ expiring-policy link (the seeded policy-number conflict is caught only because both are in one project) |
| Binder | digital PDF, 1–2 pp | `jdf` | **none** — likely `<family>_unknown` or `uncertain` | family fallback | policy_form | `binder` type: `binder_number`, `binder_period`, `bound_coverages`, `conditions` |
| Application (ACORD 90/125/…) | digital PDF or scan, form fields | `jdf` / `jdf-ocr` | **none** — applicant/prior-carrier vocabulary is not in `TYPE_KEYWORDS` | `uncertain` | policy_form | `application` type; checkbox reading (jdf-cli emits text, not form-field state) |
| Coverage summary | digital PDF | `jdf` | `property_policy` / `auto_policy` if the labels match | as declarations | policy_form | nothing structural; table cells are read as text lines (see schedule) |
| Schedule of forms / coverages | digital PDF, table-heavy | `jdf` (tables arrive as `tables[]` in the bundle and as text) | **none** — bench: routed `property_policy`, 5/9 fields; limits/deductibles inside table cells are missed | none | policy_form | table-cell extraction (`tables[]` is stored but `extract_fields` reads page text only); `schedule_of_forms` type with a form-number list |

### Claims & servicing

| Family | Modality (typical) | Router path today | Schema today | Fallback behaviour | Latency class | What is missing |
|---|---|---|---|---|---|---|
| FNOL / loss notice | digital PDF, scan, phone photo, screenshot | `jdf` / `jdf-ocr` | `auto_claim` (10 fields), `property_claim` (9 fields) | evidence reclassification | claim_packet | ACORD 1/2 form-field layouts (two-column boxes) are not benchmarked; `cause_of_loss` with an em dash is missed (bench, property) |
| CMS-1500 | digital PDF or scan | `jdf` / `jdf-ocr` | `medical_claim` (13 fields) | family gate keeps auto schemas out | claim_packet | box 24 service lines (multi-row table) — only the first row's codes; `federal_tax_id` missed when two labels share the page (bench: 11/12); UB-04 has no type |
| Repair estimate | digital PDF (CCC/Mitchell/Audatex export) | `jdf` | **none** — reads as `auto_claim` (bench: 4/5; the total under the table is missed) | none | claim_packet | `repair_estimate` type: line items, parts/labor subtotals, total; table reading |
| Adjuster notes | typed export or handwritten scan | `jdf` / `jdf-ocr` | **none** as such — reads as `auto_claim` when the note carries claim vocabulary (bench: 3/4 in a script font) | none | claim_packet (typed) / photo_signature (scan) | `adjuster_note` type; handwriting OCR (tesseract.js reads script fonts partially; real hands worse) |
| Correspondence (letters, emails) | digital PDF, .txt/.eml | `jdf` (text wrap for text-like) | **none** — usually `uncertain` | none | claim_packet | `correspondence` type; sender/recipient/date/subject; thread linking |
| Repair invoice | digital PDF or scan | `jdf` / `jdf-ocr` | **none** | none | claim_packet | `invoice` type with vendor, invoice number, total, tax; reconciliation against the estimate |
| Settlement letter / release | digital PDF | `jdf` | **none** | none | claim_packet | `settlement` type: settlement amount, release date, payee, signature |
| Subrogation demand | digital PDF | `jdf` | **none** | none | claim_packet | `subrogation` type: adverse carrier, demand amount, liability split |

### Evidence & media

| Family | Modality (typical) | Router path today | Schema today | Fallback behaviour | Latency class | What is missing |
|---|---|---|---|---|---|---|
| Property / auto damage photos | JPEG/HEIC/PNG from phones | image extension → `jdf-ocr` (HEIC/WebP not decoded) | none is right: OCR finds no text → Textract → 400 "Could not extract enough readable text" — **the upload fails** | none; there is no "this is a photo, keep it as evidence" path | photo_signature | an evidence path that stores the image with EXIF/geo/timestamp and no extraction; damage-region description (vision) is out of scope for V1 |
| Photo *of a document* (phone) | JPEG, 72–150 effective dpi | `jdf-ocr` | the document's own type (bench: `auto_policy`, 1/11 fields at 72 dpi, 9/11 at 110 dpi) | Textract when OCR reads nothing | photo_signature | deskew/perspective correction before OCR; a resolution floor that says "retake" instead of reading 1 field |
| Screenshots | PNG at screen dpi | `jdf-ocr`; material `screenshot` by dimensions | the document's type (bench: `auto_claim`, 7/8) | as above | photo_signature | none structural |
| Scanned attachments | image-only PDF, 150–300 dpi | `jdf-ocr` | the document's type (bench: 10/10 at 150 dpi) | Textract | photo_signature (manifest) / by type (code) | per-page OCR confidence is read (`ocr.blocks[].confidence`); page-level skew is not measured |
| Diagrams (accident sketch, floor plan) | image or PDF page | `jdf-ocr` | none — stray labels may trigger `uncertain` | 400 when no text | photo_signature | an evidence path (as photos) |
| Handwritten notes | scan or photo | `jdf-ocr` | the note's vocabulary decides (bench: `auto_claim`, 3/8 on typed labels + script values) | Textract | photo_signature | handwriting is not detected (`handwritten` flag exists in the vocabulary but nothing sets it); Textract handwriting is the only stronger path and is off by default |
| Signatures / initials | ink on a scan, vector stroke on a digital PDF, `/s/` text | by container | `signature` field on every schema; presence from label + bottom-20 % ink | text-only fallback when the visual probe fails | photo_signature | ink outside the bottom fifth of the page is not seen (bench: stroke at 45 % height → `missing`); initials are not looked for; e-signature markers in the upper page read `missing` |

### Mixed / noisy

| Family | Modality (typical) | Router path today | Schema today | Fallback behaviour | Latency class | What is missing |
|---|---|---|---|---|---|---|
| Poor scans (faded, low contrast) | image-only PDF | `jdf-ocr`; `low_contrast` flag when measured | the document's type (bench: grey-on-grey at 150 dpi still 11/11) | Textract when OCR reads nothing | mixed_bundle (manifest) | binarisation/contrast stretch before OCR for the cases tesseract.js loses |
| Rotated pages | scan, 90°/180°/270° | `jdf-ocr` — no orientation detection (bench: 90° page → `uncertain`, 0/11 fields; the OCR reads vertical text as noise) | none: OCR "succeeds" with garbage, so Textract is not tried | mixed_bundle | orientation detection (tesseract OSD or a text-direction heuristic) and rotate-then-OCR; PyMuPDF can rotate for free |
| Multi-document bundles | digital PDF or scan | by container | `mixed_bundle` with per-segment types (bench: 3 pp → `auto_policy` pp1–2 + `auto_claim` p3, 17/17) | segmentation by keyword change only; two same-type documents stapled together are one document | mixed_bundle | visual boundary detection (blank pages, headers, page-number resets); same-type splitting |
| Partial pages (torn, mis-fed) | image-only PDF, non-standard page size | `jdf-ocr` | the document's type (bench: 6/6 of the fields above the cut) | absent fields are `not_on_document` with a page-readability basis | mixed_bundle | nothing structural; the report already says which fields were not on the readable page |
| Low-res signatures | scan ≤ 100 dpi | `jdf-ocr`; `low_res` flag | `signature` field; `faint` when the mark is lighter than body text | — | photo_signature | as signatures above |
| Table-heavy pages | digital PDF | `jdf`; tables land in `bundle["tables"]` and the text stream | the document's type; cell values are read only when a label and value share a text line (bench: 5/9) | — | mixed_bundle | table-aware extraction: read `tables[]` (row label → column value) before the label regex; today the coverage limit "425,000" in a cell is not "Dwelling: $425,000" |

## Coverage summary (2026-09-26)

- 10 schemas exist; the plan's taxonomy names ~24 families. Eleven families have
  **no schema at all** (endorsement, cancellation, binder, application,
  schedule of forms, repair estimate, adjuster note, correspondence, invoice,
  settlement, subrogation): each is read by the nearest insurance schema or
  becomes `<family>_unknown` / `uncertain`. The benchmark counts those as
  routing misses and flags them `schema-gap` so nobody reads them as
  classifier bugs (`bench-v1`: 4 inputs).
- Photos with no text (damage evidence) **fail the upload** today; there is
  no evidence-only path.
- Rotation is the single biggest unhandled degradation: one rotated scan
  loses every field.
- Table cells are not read; two table-heavy cases miss every in-table value.
- Signature presence is bottom-of-page ink; the benchmark's pages put the
  block mid-page and are all read `missing`.
