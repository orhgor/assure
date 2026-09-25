# Assure + Parsure — V1 ICP-Ready Specification (Final)

**Competitive Context:** Securiti.ai offers enterprise data discovery, privacy, governance, and compliance (DataAI Command Center). They discover and classify sensitive data at scale across multi-cloud, SaaS, and on-premise. They are **not** a specialist in insurance document extraction quality. V1 differentiates on: multimodal evidence intake, quality-aware extraction for bad documents, verifiable confidence (real signals, not fake numbers), insurance-specific document types/fields, signature/number quality assessment, and audit trail.

**Anchor Line:** **Assure + Parsure is a multimodal evidence intelligence platform for insurance materials, with one authoritative intake router, Laya as a local calibrated triage layer behind routing, Parsure providing quality/provenance/correction/replay intelligence, and Assure providing verification and dossier assembly.**

---

## Status Legend

| Label | Meaning |
|-------|---------|
| **[EXISTING]** | Already shipped. Do not rebuild. Consume or extend. |
| **[PHASE A]** | Net-new for V1. |
| **[DEFERRED]** | Out of scope for V1. |

---

## 1. Architecture

```
Multimodal Evidence Intake (PDFs, JPG/JPEG, PNG, TIFF, phone photos,
    screenshots, handwritten images, tables, mixed bundles, email attachments)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  ONE AUTHORITATIVE MULTIMODAL INTAKE ROUTER                 │
│  [EXISTING]  Material type / modality detection             │
│  [EXISTING]  Quality probe (resolution, blur, contrast)     │
│  [PHASE A]  Laya-based triage (local calibrated decision     │
│             model behind router — fast triage, fallback,     │
│             escalation, human-review gating)                 │
│  [EXISTING]  Initial route selection (JDF or Textract)       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  [EXISTING]  Parse Execution                                │
│              - JDF: services/jdf_converter.py                │
│              - Textract: lib/textract.py                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  PARSURE — Quality/Provenance/Correction/Replay Intelligence │
│  [PHASE A]  Page quality scoring (CORE — visual signals)    │
│  [PHASE A]  Provenance (source_span, parser, version)       │
│  [PHASE A]  Correction memory                               │
│  [PHASE A]  Replay eligibility                              │
│  [PHASE A]  Corpus analytics                                 │
│  [PHASE A]  Trust inputs                                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  VERIFICATION LAYER (shared, not Parsure-only)              │
│  [EXISTING]  Z3 truth ledger (automatic, deterministic)     │
│  [EXISTING]  Red-Hat annotations (automatic)                │
│  [EXISTING]  Source conflict detection (automatic)          │
│  [EXISTING]  Multi-dimension confidence (automatic)         │
│  → Parsure CONSUMES verification output                    │
│  → Assure USES verified result to assemble dossiers         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  ASSURE — Verification + Dossier Assembly (Manual Review)   │
│  [EXISTING]  Review UI (display quality warnings + confidence)│
│  [EXISTING]  Surgical correction UI (human-in-loop edits)   │
│  [EXISTING]  Dossier assembly                               │
│  [EXISTING]  Evidence grounding + retrieval                 │
│  [PHASE A]  Dispute workflow + 72h SLA                       │
│  [PHASE A]  Final structured output + quality report         │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. One Authoritative Multimodal Intake Router

The platform is designed for **real insurance evidence, not just PDFs**. Intake covers:

| Modality | Examples | Status |
|----------|----------|--------|
| PDF (digital) | Policy documents, claims forms, typed documents | [EXISTING] |
| JPG / JPEG | Scanned documents, phone photos of documents | [PHASE A] |
| PNG | Screenshots, phone photos, diagrams | [PHASE A] |
| TIFF | High-resolution scans, fax output | [PHASE A] |
| Phone photos | Pictures of documents taken with phone camera | [PHASE A] |
| Screenshots | Photos of screen displays, digital documents | [PHASE A] |
| Handwritten images | Handwritten notes, forms, annotations | [PHASE A] |
| Tables (embedded in images/PDFs) | Coverage tables, claims tables, fee schedules | [PHASE A] |
| Mixed evidence bundles | Multiple documents, multiple modalities in one upload | [PHASE A] |
| Email attachments / exports | Documents received via email, exported from other systems | [PHASE A] |

> **The platform uses one authoritative multimodal intake router.** Parsure consumes its output and adds quality, provenance, correction, replay, and analytics intelligence on top.

The router:
- Identifies material type / modality
- Probes quality (resolution, blur, contrast, lighting, skew)
- Chooses the first path (JDF for decent digital text, Textract for scanned/bad-quality images, direct text wrap for plain text)
- Dispatches to the right backend (JDF CLI, Textract, text wrap)
- Applies Laya-based triage decisions

> The router does NOT create a second router inside Parsure. Parsure consumes the router's output.

### Laya — Local Calibrated Decision Model Behind Routing

> Laya is a **local, calibrated policy model behind the intake router** that performs fast triage and fallback decisions on multimodal evidence.

**Laya's role:**
- Fast triage (yes/no on material type, quality thresholds)
- Pick-one routing assistance (suggest JDF vs Textract vs text-wrap vs human review)
- Escalation choice (flag for human review when Laya is uncertain)
- Fallback choice (default to Textract if Laya is uncertain about quality)
- Human-review gating (when Laya flags uncertain → human review before routing)

**Laya is NOT:**
- The source of truth for facts (extraction provides facts)
- The extraction engine (JDF/Textract provide extraction)
- The verification engine (Z3/Red-Hat provide verification)
- The final compliance decision-maker (compliance review provides final decisions)

Laya is a triage layer. It helps the router make faster, better decisions. It does not own facts, extraction, verification, or compliance.

---

## 3. Ownership Reconciliation

### Router / Intake Layer — [EXISTING + PHASE A]
- Material type / modality detection — [PHASE A]
- Quality probe (resolution, blur, contrast) — [EXISTING] + [PHASE A]
- Initial route selection (JDF vs Textract vs text-wrap) — [EXISTING]
- Laya-based triage decisions — [PHASE A]

### Parsure — [PHASE A] (Intelligence Layer)
- Page quality scoring — [PHASE A]
- Provenance (source_span, parser_name, parser_version, verification_version, field_node_mapping) — [PHASE A]
- Correction memory — [PHASE A]
- Replay eligibility — [PHASE A] (design now, execution blocked on durable storage)
- Corpus analytics — [PHASE A]
- Trust inputs — [PHASE A]
- Quality-weighted confidence (verifiable, no fake numbers) — [PHASE A]

### Verification Layer — [EXISTING] (Shared, Not Parsure-Only)
- Z3 truth ledger — [EXISTING] — automatic, deterministic
- Red-Hat annotations — [EXISTING] — automatic
- Source conflict detection — [EXISTING] — automatic
- Multi-dimension confidence — [EXISTING] — automatic

> **Verification ownership clarification:** Verification lives in the verification layer (Z3, Red-Hat, source conflicts, confidence). Parsure **consumes** verification output. Assure **uses** the verified result to assemble dossiers. Parsure is **not** a second verification system.

### Assure — [EXISTING + PHASE A] (Manual Review + Dossier Assembly)
- Review UI (display quality warnings + confidence) — [EXISTING] UI + [PHASE A] display
- Surgical correction UI (human-in-loop edits) — [EXISTING]
- Dossier assembly — [EXISTING]
- Evidence grounding + retrieval — [EXISTING]
- Dispute workflow + 72h SLA — [PHASE A]
- Final structured output + quality report — [PHASE A]
- Export (JSON + CSV) — [PHASE A]

---

## 4. Multimodal Contract

The contract is the versioned bundle that Parsure produces and Assure consumes. It must be multimodal — work for PDFs, images, photos, screenshots, handwritten images, tables, and mixed bundles.

### Required Fields

| Field | Description | Status |
|-------|-------------|--------|
| `schema_version` | Contract schema version (e.g., "1.0") | [PHASE A] |
| `material_type` | Detected material type: `pdf`, `image`, `photo`, `screenshot`, `handwritten_image`, `table`, `mixed_bundle`, `text_file` | [PHASE A] |
| `modality` | Modality detail: `digital_pdf`, `scanned_pdf`, `phone_photo`, `screenshot`, `handwritten`, `table_image`, `mixed` | [PHASE A] |
| `source_kind` | Source hint: `scanned`, `text`, `image`, `photo`, `mixed` — used by router for routing decisions | [EXISTING] + [PHASE A] |
| `parser_name` | Parser used: `jdf-cli` (digital text / structured PDFs), `textract` (scanned/bad-quality images, photos, handwritten, tables) | [EXISTING] + [PHASE A] |
| `parser_version` | Parser version (e.g., "2026-09-24") | [PHASE A] |
| `verification_version` | Verification wiring version (e.g., "2026-09-24") | [EXISTING] |
| `policy_version` | Decision policy version (e.g., "v1") | [PHASE A] |
| `page_quality_score` | Quality score (0–1) per page, from visual probe + OCR confidence + text density + parse coverage + image ratio + signature quality | [PHASE A] |
| `source_span` | Source location: page + bbox (relative 0–1) or coordinates. For images, bbox normalized to image dimensions. | [PHASE A] |
| `field_node_mapping` | Bridge from node-level verification output to field-level decision input (V1: simple — one primary node per field) | [PHASE A] |

### Canonical Parser Field

Use **`parser_name`** as the field of record.

Allowed values:
- `jdf-cli` — JDF CLI parser (digital text / structured PDFs)
- `textract` — AWS Textract (scanned/bad-quality images, photos, handwritten, tables)

> The existing `parser_router.py` returns `"jdf"` and `"textract"`. The Parsure boundary layer **maps** `"jdf"` → `"jdf-cli"` at the contract serialization point.

### Source Span — Multimodal

```json
"source_span": {
  "page": 1,
  "bbox": [0.12, 0.31, 0.41, 0.37],
  "span_type": "bbox_relative"
}
```

**Rules:**
- `page` is 1-indexed
- `bbox` is **relative** (0–1), normalized to page/document dimensions regardless of original modality
- For images/photos: `bbox` is normalized to image dimensions (0–1 relative to image width/height)
- `span_type` indicates the coordinate system: `"bbox_relative"` (default), `"polygon"` (Textract polygon normalized), `"text_range"` (character offsets for pure text), `"pixel_bbox"` (raw pixel coordinates for images, optional)
- For pure text sources with no visual layout, `bbox` is omitted and `span_type` is `"text_range"` with `start_char` and `end_char` fields

### Quality-Weighted Confidence (Verifiable, Not Fake)

**Hard Requirement:** Every confidence score is based on real signals. No fabricated confidence. No hallucinated confidence. If confidence cannot be computed from real signals, it is marked as low or unavailable.

**Signals that ground confidence:**
- OCR confidence (from Textract or parser)
- Visual quality (resolution, blur, contrast from quality probe)
- Blur / contrast (from quality probe)
- Signature quality (clear/faint/incomplete/stamped/questionable/missing)
- Number readability (clear/handwritten/faded/typewritten_low_quality/printed_good)
- Z3 verification result (consistent/inconsistent/violations)
- Provenance traceability (is the field traceable to source evidence?)

**Quality-weighted confidence calculation:**
```
extraction_confidence = parser_confidence × page_quality_score
                         × number_readability_penalty (if number)
                         × z3_violation_penalty (if violation)
                         × signature_quality_penalty (if signature)
```

**Example:**
- Parser confidence: 0.95
- Page quality: 0.40 (bad scan)
- Number readability penalty: 0.7 (handwritten)
- Z3 violation penalty: 0.9 (one minor violation)
- Signature quality penalty: 1.0 (no signature on this field)
- `extraction_confidence = 0.95 × 0.40 × 0.7 × 0.9 × 1.0 = 0.24`
- `confidence_basis = "parser_confidence (0.95) × page_quality (0.40) × handwritten_penalty (0.70) × z3_penalty (0.90) = 0.24"`

**Default when no signal available:**
- If parser confidence is not available: use default 0.85 (JDF) or 0.80 (Textract)
- If page quality is not available: use 0.5 (neutral)
- If no other signals: `extraction_confidence = 0.50`, `confidence_basis = "no_signal_available — conservative default"`

---

## 5. Field State vs Routing Action (Strict Split)

### field_state = Semantic Result

| Value | Meaning |
|-------|---------|
| `accepted` | Field is verified, trustworthy, no review needed |
| `partial` | Field is partially verified, needs review |
| `unverified` | Field is not verified, needs review |
| `disputed` | Field is under dispute |
| `rejected` | Field is rejected (Z3 violation, bad quality, etc.) |

### routing_action = Operational Response

| Value | Meaning |
|-------|---------|
| `manual_review` | Send to human reviewer |
| `adjudicator_queue` | Send to adjudicator queue |
| `compliance_review` | Send to compliance review |
| `retry_parsure` | Retry parsing via another path |
| `replay_later` | Queue for replay later |

> **Critical distinction:** `manual_review` is a **routing_action**, NOT a `field_state`. "Manual review" means "send to human reviewer." It is NOT "the field is uncertain." A field can be `unverified` (field_state) AND have routing_action `manual_review`.

### Decision Policy (V1 — 3 Rules)

| Rule | Condition | field_state | routing_action |
|------|-----------|-------------|----------------|
| 1 | `verification_confidence ≥ 0.8` AND `extraction_confidence ≥ 0.75` AND field NOT compliance-bound AND no Z3 violation | `accepted` | `none` |
| 2 | `verification_confidence < 0.8` OR `extraction_confidence < 0.75` OR field compliance-bound OR Z3 violation | `unverified` (or `rejected` if Z3 violation) | `manual_review` (or `compliance_review` if Z3 violation) |
| 3 | Z3 violation → `rejected` + `compliance_review` | `rejected` | `compliance_review` |

---

## 6. Fallback Behavior for Ugly Materials (V1)

### Bad Scans

| Condition | Output |
|----------|--------|
| Low resolution (< 200 DPI) | Route to Textract (OCR better than JDF for low-res). Flag `low_res` in quality report. If text is unreadable even after OCR → `manual_review`. |
| Blurry (Laplacian variance < threshold) | Route to Textract. Flag `blurry`. If OCR confidence is low → `manual_review`. |
| Low contrast (std dev < threshold) | Route to Textract. Flag `low_contrast`. If OCR fails → `manual_review`. |
| Crooked / skewed | Route to Textract (OCR handles skew better). Flag `skewed`. |
| Glare / bright spots | Route to Textract. Flag `glare`. If affected region has critical field → `manual_review`. |
| Noisy / fax banding | Route to Textract. Flag `noisy`. If OCR output is garbled → `manual_review`. |

### Faint Signatures

| Condition | Output |
|----------|--------|
| Signature present but faint (ink density < 30% of typical) | Detect signature, flag `signature_quality = "faint"`, `review_required = true` for that signature region. Extraction continues, but signature is flagged. |
| Signature incomplete (truncated, on wrong line) | Flag `signature_quality = "incomplete"`, `review_required = true`. |
| Stamped signature | Flag `signature_quality = "stamped"`, `review_required = true` (stamp ≠ handwritten signature). |
| No signature where expected | Flag `signature_quality = "missing"`, `review_required = true`. |

### Unreadable Numbers

| Condition | Output |
|----------|--------|
| Handwritten numbers | Flag `number_quality = "handwritten"`, apply readability penalty (0.7×), `review_required = true`. |
| Faded numbers (OCR confidence < 0.6, low local quality) | Flag `number_quality = "faded"`, apply penalty (0.6×), `review_required = true`. |
| Typewritten on bad page (OCR ok but page quality low) | Flag `number_quality = "typewritten_low_quality"`, apply penalty (0.8×), `review_required = true` if confidence < threshold. |
| Completely unreadable (OCR fails, no value) | `value = null`, `confidence = 0.0`, `review_required = true`, `reason = "number unreadable — manual review required"`. NO HALLUCINATION. |

### Low-Resolution Photos

| Condition | Output |
|----------|--------|
| Phone photo of document, low res (< 200 DPI effective) | Route to Textract. Flag `low_res`, `photo`. If OCR confidence is high enough → extract with low confidence. If OCR fails → `manual_review`. |
| Phone photo, decent quality (> 200 DPI effective) | Route to Textract (camera photos are images, not digital PDFs). Flag `photo`. Extract with quality-weighted confidence. |
| Phone photo with glare on critical field | Flag `glare`. If critical field (policy number, VIN, amount) is in glare region → `manual_review` for that field. |

### Mixed Bundles

| Condition | Output |
|----------|--------|
| Multiple documents in one upload (e.g., policy + claim + photo) | Detect multiple documents (by page boundaries, content changes). Classify each separately. Extract fields per document. Flag if bundle parsing is uncertain. |
| Multiple modalities in one upload (e.g., PDF + phone photo) | Detect modality per page/region. Route each to appropriate parser. Extract per modality. Flag mixed bundle. |

### Tables Embedded in Images

| Condition | Output |
|----------|--------|
| Table in a scanned image / photo | Route to Textract (OCR + table detection). Extract table cells. Flag `table_image`. If table structure is unclear → `manual_review` for that table. |
| Table in a PDF (digital) | JDF may extract table structure directly. Flag `table_pdf`. |
| Table in a handwritten image | Route to Textract. Flag `handwritten_table`. `manual_review` likely required. |

---

## 7. Replay / Storage Dependency (V1)

> Replay is **designed now** but **execution depends on durable artifact storage**. If storage is still stubbed/local-first, replay is limited or deferred.

**Replay is designed now (V1):**
- Replay eligibility flags (low confidence, corrected, policy changed, new parser version)
- `replay_history` field in artifact
- Interface signature for replay orchestrator
- Replay eligibility logic encoded in decision policy

**Replay execution is blocked until:**
- Durable artifact storage (S3 production, not stubbed) is available
- Local storage is the default/show-only path today

**V1 replay limitations:**
- A field can be flagged for replay, but actual re-parsing is deferred until storage is ready
- `replay_eligible = true` can be set, but `replayed = false` until storage supports it
- `replay_history` records intent, not execution

---

## 8. Final No-Duplication Guardrails (V1 Acceptance Criteria)

Phase V1 implementation is acceptable only if **all** of the following are true:

1. **Do not duplicate the router** — one authoritative multimodal intake router exists. Parsure consumes its output. No second router inside Parsure.
2. **Do not duplicate verification wiring** — Z3, Red-Hat, source conflicts, confidence scoring are in the verification layer. Parsure consumes their output. Assure uses verified result for dossiers. No second verification system in Parsure.
3. **Do not collapse parser / quality / verification into one vague confidence bucket** — `extraction_confidence` (parser), `page_quality_score` (visual quality), `verification_confidence` (Z3), `provenance_confidence` (traceability), `signature_quality` (signature), `number_quality` (number readability) are all separate. No collapsing.
4. **Do not keep PDF-only assumptions for a multimodal client** — the contract, router, and extraction handle PDFs, images, photos, screenshots, handwritten images, tables, and mixed bundles. Plymouth Rock is not a PDF-only use case.
5. **Confidence is grounded in real signals** — OCR confidence, visual quality, blur/contrast, signature quality, number readability, Z3 result, provenance traceability. No fabricated confidence. No hallucinated confidence.
6. **No hallucination** — if a field cannot be found, value=null, confidence=0, review=true, reason="field not found". No fake numbers, no guessed VIN, no made-up coverage.
7. **field_state and routing_action are strictly separate** — `manual_review` is a routing_action, not a field_state.
8. **Laya is behind routing, not the router** — Laya is a local calibrated triage model. It assists routing decisions. It is not the router itself, not the extraction engine, not the verification engine, not the final compliance decision-maker.

---

## 9. V1 Implementation Sequence

1. **[EXISTING]** — Verify ingestion, routing, parse, Z3, Red-Hat, UI are in place
2. **[PHASE A]** — Implement multimodal intake router: material type/modality detection, quality probe, route selection, Laya-based triage
3. **[PHASE A]** — Implement visual quality probe: resolution, blur, contrast. Route bad quality → Textract.
4. **[PHASE A]** — Implement page quality scoring: visual quality + OCR confidence + text density + parse coverage + image ratio + signature quality. Quality-weighted confidence.
5. **[PHASE A]** — Implement signature detection + quality assessment: label-based + bottom-20% fallback, clear/faint/incomplete/stamped/questionable/missing.
6. **[PHASE A]** — Implement number quality assessment: OCR confidence + local page quality + handwriting detection, clear/printed_good/handwritten/faded/typewritten_low_quality, penalties.
7. **[PHASE A]** — Implement field extraction with NO-HALLUCINATION: ICP fields per type, quality-weighted confidence, value=null/confidence=0/review=true when field not found.
8. **[PHASE A]** — Implement verifiable confidence: confidence_basis field showing calculation, conservative, explained in UI.
9. **[PHASE A]** — Implement basic traceability per field: source_span, parser info, Z3 result.
10. **[PHASE A]** — Implement Z3 verification with V1 check set: cross-field rules per document type, source consistency, cross-document consistency, default 0.85 when no check applies.
11. **[PHASE A]** — Implement simple decision policy (3 rules).
12. **[PHASE A]** — Implement version stamp + multimodal contract fields + quality report on artifact.
13. **[PHASE A]** — Implement document classification (keyword heuristic, uncertain fallback).
14. **[PHASE A]** — Implement VIN validation for auto documents.
15. **[PHASE A]** — Implement coverage plausibility rules (6 rules for auto insurance).
16. **[PHASE A]** — Implement cross-document conflict detection (3 shared fields, conflicts[] array, no auto-dispute).
17. **[PHASE A]** — Implement Assure review UI with quality warnings + confidence_basis + review flags + multimodal handling.
18. **[PHASE A]** — Implement review queue / batch table view.
19. **[PHASE A]** — Implement simple correction record + dispute workflow (72h SLA).
20. **[PHASE A]** — Implement correction history per field.
21. **[PHASE A]** — Implement classification override UI.
22. **[PHASE A]** — Implement audit log (10 event types, SQLite, UI tab + API).
23. **[PHASE A]** — Implement final structured output + quality report + review summary + confidence_basis per field.
24. **[PHASE A]** — Implement CSV + JSON export.
25. **[PHASE A]** — Implement fallback behavior for ugly materials (defined in Section 6).
26. **[PHASE A]** — Implement golden set (5-10 docs) + validation script.
27. **[PHASE A]** — Implement corpus analytics (counters + accuracy from golden set).
28. **[PHASE A]** — Implement field_source_node_id.

**Deferred:** surgical re-parse, field_node_mapping (full), trust model with decay/nightly, automation tiers beyond auto-parse/auto-verify, corpus analytics (full), full revisioning, external system integration, bulk queue orchestration, summarization, specialized table/form tools.

---

## 10. V1 Go / No-Go Criteria

1. Multimodal intake router works: material type/modality detection, quality probe, route selection, Laya triage
2. Visual quality probe works: bad quality → Textract, decent quality + text → JDF
3. Page quality scoring produces 0–1 scores with quality flags
4. Quality-weighted confidence: extraction_confidence reflects page quality, not just parser confidence. confidence_basis shows calculation.
5. No hallucination: fields not found → value=null, confidence=0, review=true, reason="field not found". No fake numbers.
6. Verifiable confidence: every confidence score is traceable to real signals. No inflated confidence. Conservative. confidence_basis shown.
7. Signature detection + quality assessment works: detects signatures, assesses quality, flags bad ones
8. Number quality assessment works: detects number regions, assesses readability, flags bad numbers
9. Field extraction produces structured fields with quality-weighted confidence
10. Basic traceability (3 essentials) on each field — client can go back to source
11. Z3 + Red-Hat verification works
12. Z3 V1 check set implemented: cross-field rules per document type, source consistency, cross-document consistency, default 0.85 when no check applies
13. Simple decision policy (3 rules) implemented with quality-weighted confidence. field_state and routing_action strictly separate.
14. Document classification works (keyword heuristic, uncertain fallback)
15. Multimodal contract fields implemented: material_type, modality, source_kind, parser_name, page_quality_score, source_span, field_node_mapping
16. VIN validation works for auto documents
17. Coverage plausibility rules implemented (6 rules for auto insurance)
18. Cross-document conflict detection works (3 shared fields, conflicts[] array, no auto-dispute)
19. Quality report + review summary + confidence_basis on artifact
20. Review UI displays quality warnings, per-page quality, signature quality, number quality, quality-weighted confidence, confidence_basis, review flags. Handles multimodal documents.
21. Fallback behavior for ugly materials implemented (defined in Section 6).
22. Correction UI works (human edits, correction recorded)
23. Correction history per field shown (original + corrected + who/when/why)
24. Classification override UI works (dropdown to change document type)
25. Dispute workflow with 72h SLA implemented
26. Audit log implemented (10 event types, SQLite, UI tab + API endpoint)
27. Final structured output + quality report + review summary + confidence_basis per field
28. CSV + JSON export implemented
29. Golden set + validation script implemented. Accuracy reported per field and per document type.
30. Output reviewed with at least one insurance client (Plymouth Rock or similar) — client confirms: "This handles my bad documents (scans, photos, handwritten notes, signatures, tables), the confidence scores are honest and verifiable, and I can trust the extraction or know exactly what to review."

---

## 11. Final Boundary

**Intake Router (one authoritative router — [EXISTING + PHASE A]):**
- Material type / modality detection
- Quality probe (resolution, blur, contrast)
- Initial route selection (JDF vs Textract vs text-wrap)
- Laya-based triage decisions (fast yes/no, fallback, escalation, human-review gating)

**Laya (local calibrated triage model behind router — [PHASE A]):**
- Fast triage (yes/no on material type, quality thresholds)
- Pick-one routing assistance (suggest JDF vs Textract vs text-wrap vs human review)
- Escalation choice (flag for human review when uncertain)
- Fallback choice (default to Textract if uncertain)
- Human-review gating (when Laya flags uncertain → human review before routing)
- NOT the source of truth for facts, extraction engine, verification engine, or final compliance decision-maker

**Parsure (automatic intelligence layer — [PHASE A]):**
- Page quality scoring (0–1, quality flags, quality-weighted confidence)
- Provenance (source_span, parser_name, parser_version, verification_version, field_node_mapping)
- Correction memory
- Replay eligibility (design now, execution blocked on durable storage)
- Corpus analytics (counters + accuracy from golden set)
- Trust inputs
- Quality-weighted confidence (verifiable, no fake numbers)
- NO HALLUCINATION (value=null/confidence=0/review=true when field not found)

**Verification Layer (shared, not Parsure-only — [EXISTING]):**
- Z3 truth ledger (automatic, deterministic)
- Red-Hat annotations (automatic)
- Source conflict detection (automatic)
- Multi-dimension confidence (automatic)
- Parsure CONSUMES verification output
- Assure USES verified result to assemble dossiers
- NOT a second verification system in Parsure

**Assure (manual review + dossier assembly — [EXISTING + PHASE A]):**
- Review UI with quality warnings + confidence explanation
- Surgical correction UI (human-in-loop edits)
- Correction history per field
- Dispute workflow (72h SLA)
- Classification override UI
- Review queue / batch table view
- Final structured output + quality report + review summary + confidence_basis per field
- CSV + JSON export
- Audit log display
- Dossier assembly
- Evidence grounding + retrieval

**Parsure computes, assesses quality, and flags. Assure displays, acts, and delivers.**

---

## 12. Competitive Differentiation

| Dimension | Securiti.ai | V1 (Assure + Parsure) |
|-----------|-------------|----------------------|
| **Primary focus** | Data security, privacy, governance, compliance at enterprise scale | Multimodal evidence intelligence platform for insurance materials |
| **Document quality handling** | General data discovery — may extract without quality context | Quality-aware: visual quality probe, page quality scoring, quality-weighted confidence, quality warnings per field/page |
| **Confidence scores** | May provide generic confidence | Verifiable confidence: confidence_basis shows calculation, conservative, explained, no fake numbers |
| **Hallucination handling** | Not specified | Hard requirement: no hallucination, value=null/confidence=0/review=true when field not found |
| **Signature assessment** | Not specified | Signature detection + quality (clear/faint/incomplete/stamped/questionable/missing), flagged |
| **Number quality** | Not specified | Number quality assessment (clear/handwritten/faded/typewritten_low_quality/printed_good), flagged, quality-weighted |
| **Document types** | General (any data) | Insurance-specific: auto policy, auto claim, auto title, property policy, property claim, deed, mortgage, title, closing |
| **Multimodal intake** | General data discovery | PDFs, JPG/JPEG, PNG, TIFF, phone photos, screenshots, handwritten images, tables, mixed bundles, email attachments |
| **Fallback for ugly materials** | Not specified | Explicit fallback behavior: bad scans → Textract + flag, faint signatures → flag + review, unreadable numbers → null + review, low-res photos → Textract, mixed bundles → per-document/per-modality extraction, tables in images → Textract + flag |
| **Audit trail** | Enterprise audit (general) | Field-level audit: source_span, parser, version, correction history, dispute history, 10 event types |
| **ICP readiness** | General enterprise | V1 designed for Plymouth Rock: bad scans, phone photos, handwritten notes, signatures, tables, mixed evidence bundles, insurance claim/policy/correspondence materials |

**V1's edge:** Multimodal evidence intake for bad documents, verifiable confidence (no fake numbers), no-hallucination guarantee, signature + number quality assessment, insurance-specific document types and fields, field-level audit trail, explicit fallback behavior for ugly materials, Plymouth Rock use case anchored.

---

## 13. V1 Value Proposition

**Before V1 (or with Securiti.ai):** Insurance company uploads a bad scan, phone photo, or handwritten document. Gets back extracted data with no quality context. Doesn't know what's trustworthy. Might get fake or inflated confidence. Manual review of everything because nothing is trusted. PDF-only assumptions don't match real evidence.

**After V1:** Insurance company uploads a bad scan, phone photo, screenshot, handwritten note, or mixed bundle. Gets back:
- Material type / modality detection: "This is a phone photo of a handwritten insurance policy."
- Quality report: "This is a bad scan. Low resolution, blurry, low contrast, glare on the policy number."
- Per-page quality scores + flags
- Quality-weighted extraction: "The parser extracted ABC123456 as the policy number, but the page is too blurry to trust it (confidence 0.38). Review it." Confidence basis shown: parser 0.95 × page quality 0.40 = 0.38.
- Signature assessment: "The signature on page 3 is faint. Verify it."
- Number quality: "The premium is handwritten on a bad page. Verify it."
- Z3 check: "The premium seems too low for the coverage. Flagged."
- No hallucination: If a field isn't found, value=null, confidence=0, review=true. No made-up data.
- Review summary: "12 of 15 fields need review. Here's why."
- Audit trail: Every field traceable to source. Every correction recorded. Every dispute tracked. Every event logged.

**That's the value.** Multimodal evidence intake from bad documents, with honest, verifiable confidence. No fake numbers. No hallucination. Insurance-specific. Traceable. Auditable. Plymouth Rock ready.

---

## Files

- `assure_parsure_v1_icp_spec.md` — V1 ICP-ready specification (this file)
- `prompt_matrix/services/quality_probe.py` — Visual quality probe + page quality scoring + quality-weighted confidence + signature quality + number quality
- `prompt_matrix/services/field_extractor.py` — Classification + field extraction (no-hallucination) + verifiable confidence + decision policy (3 rules) + ICP taxonomy
- `prompt_matrix/services/v1_orchestrator.py` — V1 parsing orchestrator (quality-aware routing, classification, extraction, decision policy, output generation)
