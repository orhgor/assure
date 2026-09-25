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
