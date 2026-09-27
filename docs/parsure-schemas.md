# Parsure schemas, tables, discovery, export names (2026-09-27)

Plan Parts 3, 4 and 8.5 of the customer's execution plan
(`todos/fable_execution_plan.md`). Code: `services/schema_registry.py`,
`prompt_matrix/schemas/*.json`, `services/table_extraction.py`,
`services/field_discovery.py`, `services/export_names.py`; the hooks in
`v1_orchestrator.extract_segment_fields` / `build_report`; route
`GET /api/parsure/schemas`. Tests: `tests/test_schema_registry.py`,
`tests/test_table_extraction.py`, `tests/test_export_names.py`.

## 1. Schema registry — the taxonomy as data

The document-type taxonomy the extractor runs with (`fx.DOCUMENT_TYPES`,
`fx.TYPE_FAMILY`, `fx.TYPE_KEYWORDS`, `fx.FIELD_TAXONOMY`) is built at import
by `schema_registry.reload()` from two sources, in this order:

1. **Built-in** schemas — the `_f()` specs in `services/field_extractor.py`
   (kept there so their regex history stays with the code); snapshotted as
   `fx._BUILTIN_*` before the rebind.
2. **Runtime** schemas — every `*.json` in `prompt_matrix/schemas/` (shipped
   with the image) and in `ASSURE_SCHEMA_DIR` (one or more directories,
   `os.pathsep`-separated). Files are read in directory order, sorted by name.

Order is significant: `classify_document` breaks keyword ties by taxonomy
order, so built-ins keep their original order and runtime types follow. A
runtime schema may reuse a built-in type name; it then replaces it (logged,
`overrides: "builtin"` on the schema).

### File format (`parsure-schema-v1`)

```json
{
  "format": "parsure-schema-v1",
  "document_type": "cancellation_notice",
  "family": "auto",
  "label": "Cancellation notice",
  "description": "what wording this anchors on and why the type exists",
  "keywords": ["notice of cancellation", "cancellation effective date", "unearned premium", "..."],
  "fields": [
    {"name": "policy_number", "use": "auto_policy.policy_number"},
    {"name": "cancellation_reason", "label": "Reason for cancellation", "field_type": "text",
     "anchors": ["reason\\s+for\\s+cancellation", "cancellation\\s+reason"], "compliance_bound": false}
  ]
}
```

| key | rule |
|---|---|
| `document_type` | `^[a-z][a-z0-9_]{1,63}$`; not `uncertain`, `mixed_bundle`, `unknown`, `other`, `<family>_unknown` |
| `family` | one of `field_extractor.DOCUMENT_FAMILIES` (`auto`, `property`, `real_estate_transaction`, `medical`). Families are **not** extensible here: `document_family` is tuned on `FAMILY_CUES`, and a type of a family the gate cannot name would never be chosen |
| `keywords` | ≥ 3 phrases (the classifier needs `MIN_KEYWORD_MATCHES` = 3 hits to name a type); matched case-insensitively as whole phrases. Confidence = hits ÷ keywords, capped at 0.9, so a long list of words the document does not carry *lowers* confidence |
| `fields[]` | 1–40; `name` `^[a-z][a-z0-9_]{0,63}$` unique; `field_type` ∈ `FIELD_TYPES` (`text number money date vin name signature codes`); `anchors` ≥ 1 regex, each compiled exactly as the label pass compiles it (`(?<![a-z])<anchor>(?!'|[a-z])<sep><value>`) — a regex that clashes with the `sep`/`val` group names is rejected; `compliance_bound` bool |
| `fields[].use` | `"<builtin_type>.<field_name>"` copies a built-in spec (anchors, type, compliance flag); `name`/`label`/`compliance_bound` may override |

**Validation is strict, failure is quiet.** A file that does not parse or
breaks a rule is skipped with a logged reason; it is listed under `rejected`
on `GET /api/parsure/schemas`. The extractor imports the registry, so a bad
file must never fail the ingest worker's import. A registry bug leaves the
static taxonomy bound (`field_extractor._bootstrap_schema_registry`).

Repository conventions the tests enforce on every loaded type
(`tests/test_field_extractor.py::test_taxonomy_covers_every_icp_type_with_compliance_flags`):
6–13 fields, and a compliance-bound `signature` field — every insurance form
has a signature block, and its absence is reported honestly by the label pass.

### Adding a document type — no code change

1. Write the wording the type is recognised by (its own words, not the words
   it shares with its neighbours — see the `schedule_of_forms` description for
   why `homeowners`/`deductible` are *not* schedule keywords).
2. Write the label anchors from the document's own labels (`"Estimate Date"`,
   `"Reason for Cancellation"`), using `use` for shared fields.
3. Drop the file in `prompt_matrix/schemas/` (ships with the image) or in
   `ASSURE_SCHEMA_DIR` (per deployment). Restart, or call
   `schema_registry.reload()`; `GET /api/parsure/schemas` shows the result.
4. Add the document as a benchmark case (`bench/manifest.json`) before or with
   the schema — `docs/benchmark.md` governance rule 2.

Programmatic: `schema_registry.register_schema(dict)` validates, registers
for the process and rebinds; `SchemaError` on a bad document;
`unregister_schema(name)` removes it. `tests/test_schema_registry.py::
test_new_document_type_by_registration_needs_no_code_change` is plan 7.3.

### The first four runtime schemas (the benchmark's schema gaps)

| type | family | anchored on (bench fixture) | what it reads |
|---|---|---|---|
| `endorsement` | auto | `AUTO_ENDORSEMENT`: "This endorsement changes the policy", "Endorsement Effective Date", "Vehicle removed / added", "Additional Premium for the remainder of the term" | the **added** vehicle's VIN and description first (the page names two; the added one is the truth), endorsement number, change description, premium |
| `cancellation_notice` | auto | `AUTO_CANCELLATION`: "NOTICE OF CANCELLATION", "hereby notified", "Cancellation Effective Date", "Reason for Cancellation", "Amount past due", "Unearned Premium" | cancellation date/reason, amount due, unearned premium, mailing address |
| `repair_estimate` | auto | `REPAIR_ESTIMATE`: "REPAIR ESTIMATE", "Estimate Date", "Parts/Labor subtotal", "Tax (…)", "Estimated Damage (parts + labor + tax)" | estimate date, parts/labor totals, tax, estimated damage (with the parenthetical) |
| `schedule_of_forms` | property | `COVERAGE_SCHEDULE_*`: "SCHEDULE OF COVERAGES AND FORMS", "Total Annual Premium", "Forms and endorsements made part of this policy" | policy/insured/period/premium by label; dwelling, personal property, deductible **from the table** |

The manifest's expected type for the endorsement case is `endorsement` (not
`auto_endorsement`); the schema follows the frozen manifest. A cancellation
notice for a property policy would be gated out (one family per type) and
needs its own schema — recorded here as a known gap.

## 2. Table-aware extraction

**Measured shape (jdf-cli 0.2.3, 2026-09-27, `bench/cases` repair estimate
and coverage schedule):** a ruled table is one `pages[].elements[]` entry of
`type: "table"` with `headers[]`, `rows[][]`, `columns[]`, `position`,
`width`, `style.fontSize` — no `content`, no `height`. `page_layout` skips it
(no text), so **table cells are never in the page text** and the label pass
cannot see them; the chunker emits a `types: ["table"]` chunk (`p1e2`) whose
text is a `Header: cell | …` flattening — that chunk id is the table's node
address. Ruled column lines produce phantom blank-header columns holding the
value of the header to their left (`["Hours", "", "Parts $", ""]`).

`table_extraction.collect_tables(bundle)` reads `bundle["jdf_source"]` (the
raw jdf-cli document `pdf_ingest` keeps beside the saved tree) or
`bundle["jdf"]` pages, else the tree's table nodes (which now carry the grid —
`jdf_converter.jdf_to_document_tree` copies the k-th table element of a page
into the k-th table chunk's node). Each table is published on the report:

```json
"tables": [{
  "table_id": "tbl-p1e2:7b35229fc2b2",         // "tbl-" + eid-v1 derivation (chunk id, page, bbox, grid text)
  "node_id": "p1e2", "chunk_id": "p1e2", "element_id": "p1e2:7b35229fc2b2", "id_policy": "eid-v1",
  "page": 1,
  "bbox": [0.0816, 0.2028, 0.9184, 0.3863],
  "bbox_basis": "position/width from jdf-cli; height estimated as (8 rows + header) × 8.5 pt × 1.9",
  "headers": ["Coverage", "Form", "Limit", "Deductible", "Premium"],
  "rows": [["Coverage A · Dwelling", "HO 00 03", "$425,000", "$2,500", "$1,412.00"], "…"],
  "caption": null,
  "source": "jdf_element",                      // or "tree_node"
  "layout_repair": {"columns_in": 7, "columns_out": 5, "columns_merged": 2, "columns_dropped": 2, "basis": "…"},
  "quality": {"status": "ok", "basis": "5 headers, 8 rows, 40 cells", "score": 1.0, "issues": [],
              "header_count": 5, "row_count": 8, "cell_count": 40, "empty_cells": 0}
}]
```

`quality.status` ∈ `ok | irregular_rows | no_headers | unreadable` (plan 3.3);
`issues` also carries `many_empty_cells` (> 30 % blank). `score` is the plan's
1.0 − 0.3/0.2/0.2 arithmetic, not a probability. The grid repair is stated,
never silent. Ids are the `eid-v1` derivation, so two ingests of the same
bytes name the same table.

### Which fields, which cell

Only **money / number / date** fields the label pass left `None` are offered
(`fill_from_tables`), and the pass runs **between the label pass and the
grounded LLM pass** in `extract_segment_fields`, so the model is asked about
fewer fields. Headers and row labels are matched against the field's own
anchors — a schema author writes nothing extra for tables. `evidence.pick`
names the rule that chose the cell:

| pick | rule | confidence |
|---|---|---|
| `total_row` | a header matches the anchor and a row labelled `Total…` exists | full |
| `single_row` | header match, one row carries a value | full |
| `unanimous` | header match, every row carries the same value | full |
| `first_row` | header match, rows disagree, no total row → first parseable row | × `TABLE_AMBIGUOUS_FACTOR` 0.85 (0.85 × 0.85 = 0.72 < 0.75: never auto-accepted; the basis says why) |
| `row_label` | the anchor is found in the row's first cell (`Coverage A · Dwelling` → `dwelling_coverage`); the cell under a header matching the anchor, else under a `limit/amount/…` header (`VALUE_HEADERS`), else the first cell parsing as the type | full |

The field record is `field_extractor`'s contract shape (built from
`_empty_field` + the found-field keys — never a second shape):

```json
{"name": "dwelling_coverage", "value": 425000.0, "raw": "$425,000", "extraction_method": "table",
 "provenance_confidence": 1.0, "quality_source": "page_quality",
 "source_span": {"page": 1, "span_type": "table_cell", "table_id": "tbl-p1e2:…", "row": 0, "col": 2,
                 "element_id": "p1e2:…", "node_id": "p1e2", "header": "Limit", "row_label": "Coverage A · Dwelling", "bbox": [ "…" ]},
 "field_source_node_id": "p1e2", "element_id": "p1e2:…",
 "grounding_quote": "Coverage A · Dwelling — Limit: $425,000",
 "grounding_span": {"page": 1, "table_id": "tbl-p1e2:…", "row": 0, "col": 2, "element_id": "p1e2:…", "node_id": "p1e2"},
 "grounding_model": "table", "grounding_source": "table",
 "evidence": {"kind": "found", "method": "table", "pick": "row_label", "basis": "row 'Coverage A · Dwelling' matched the Dwelling coverage label; header 'Limit' names a money column", "table_quality": "ok", "…": "…"}}
```

`execution.tables` on every report:

```json
"execution": {"tables": {"status": "completed",          // or "not_run" | "failed"
                         "tables": 1, "fields_offered": 3, "fields_from_tables": 3,
                         "fields_filled": ["dwelling_coverage", "personal_property_coverage", "deductible"],
                         "reason": null}}                 // "no table elements in the parse bundle" when not_run
```

A classification override (`reextract_for_type`) re-runs the pass with the
report's own `tables[]`. Tree addressing: the table field's
`field_source_node_id` is the chunk id; the saved tree's table node carries no
`meta.chunk_id` (the `JDFTableNode` model has no `meta`), so `tree_node_id`
stays `None` for table fields — anchored (no orphan), not deep-linkable yet.

Measured on `bench-v1` (2026-09-27): the coverage schedule's three in-table
values (dwelling, personal property, deductible) go from 0/3 to 3/3; the
repair estimate's totals are label hits under the table and were never table
reads.

## 3. Dynamic field discovery (`<family>_unknown`, `uncertain`)

`field_discovery.run_discovery` runs in the same hook for a segment no schema
fits. It lists the `Label: value` pairs on the page as
`report["discovered_fields"]` — **not** taxonomy fields: no state, no routing,
no confidence, not counted in `review_summary`/`fields_total`,
`taxonomy_field: false` on each.

```json
"discovered_fields": [{"name": "roof_condition", "label": "Roof Condition", "value": "Fair, curling shingles", "page": 1,
                       "span": {"page": 1, "span_type": "bbox_relative", "start_char": 74, "end_char": 96, "bbox": ["…"], "node_id": "c1", "element_id": "c1:…"},
                       "method": "heuristic_label_value", "taxonomy_field": false, "grounding_source": "text"}],
"execution": {"discovery": {"status": "completed", "pairs": 6, "heuristic": 6, "llm_grounded": 0, "model": null,
                            "model_status": "skipped", "reason": "PARSURE_LLM_EXTRACTION is off"}}
```

`heuristic_label_value`: a line regex (label ≤ 48 chars, colon, value on the
line), labels and blanks skipped, ≤ 40 pairs. `llm_grounded_discovery` only
when `PARSURE_LLM_EXTRACTION=1` and the path allows a model call: the model
lists `{label, value, quote}`; a pair is kept only when the quote is found
verbatim in a page (`llm_extraction.find_verbatim`) and the value inside the
quote — the stored value is the document's characters, `grounding_quote` /
`grounding_model` are carried. Typed documents get
`execution.discovery.status = "not_run"`.

## 4. `intake_extra` (plan 4.3)

`report["intake_extra"]` = the `intake` keys the report build did not consume
(`v1_orchestrator.CONSUMED_INTAKE_KEYS`: `parser`, `parser_name`,
`material_type`, `modality`, `source_kind`, `visual_pages`, `laya`), verbatim,
or `null`. The router's `material_basis` therefore lands here on every
router-driven ingest — that is the audit trail, not a bug.

## 5. Export filename convention (plan 8.5)

`export_names.build_export_filename(report, ext, suffix=None)`:

```
<type>_doc-<document_id[:12]>[_policy-…][_claim-…][_insured-…][_eff-…][_loss-…][_vin-…][_<suffix>]_<YYYYMMDDTHHMMSSZ>.<ext>
auto-policy_doc-abc123def456_policy-AP-2025-0001_insured-John_Q_Sample_eff-2025-01-15_20260927T150000Z.json
title_doc-def456_20260927T150000Z.json                     ← nothing found: no placeholder parts
```

Only data points **actually found** appear (value not `None`, not
`rejected`, not `schema_mismatch`); every part is slugified to
`[A-Za-z0-9._-]` (NFKD → ASCII, whitespace → `_`, quotes dropped); ≤ 150
characters — the insured name is shortened first, then points are dropped
from the right; type, document id, timestamp and extension always stay.

Applied to: `GET …/parsure/<report_id>/export` (json/csv); the project export
(`build_project_export_filename`: `parsure_<project>_<n>-docs[_<type>][_<state>]_<ts>.<ext>`);
and, when the project has an intake report, the dossier PDF
(`…_verification-dossier_<ts>.pdf`), the JDF sidecar (`…_jdf_<ts>.json`) and
the bundle zip (`…_dossier_<ts>.zip`) in `routers/export_routes.py` — without
a report the historical `<project>-<version>-dossier` name stands.

## Anti-claims this adds

- A table field is never "verified by the table": `verification_confidence`
  comes from Z3/rules exactly as for a label hit; the table is provenance.
- `first_row` is a stated guess among rows that disagree and never
  auto-accepts. A table with `quality.status = unreadable` fills nothing.
- Discovered pairs are not fields: they do not raise `fields_found`, and the
  model path adds nothing the page does not spell.
- A schema that the registry skipped is not "unsupported silently": the
  reason is on `/api/parsure/schemas` and in the log.
