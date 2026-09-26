# Parsure benchmark — frozen set, gates, governance

The customer's plan (§8 benchmark and measurement, §7 P1 "parsing must stay
fast") asks for a frozen, versioned benchmark with launch gates and per-class
metrics. This is it: `bench/manifest.json` (the set, `bench-v1`, frozen
2026-09-26), `bench/cases/` (the generators), `scripts/benchmark.py` (the
harness), `bench/results/` (one JSON per run). The corpus taxonomy the cases
cover is `docs/corpus.md`.

## Running it

```bash
docker compose -f docker-compose.dev.yml up -d            # PostgreSQL :5432
DATABASE_URL=postgresql://assure:assure@localhost:5432/assure \
ASSURE_PG_SCHEMA=bench_run \
.venv/bin/python scripts/benchmark.py
```

- The harness runs the **real pipeline in-process**:
  `services.pdf_ingest.ingest_pdf_for_project` — the worker's function — with
  `PARSE_ASYNC=0`, one throwaway project per case (`bench-<run id>-<case id>`),
  the router, jdf-cli 0.2.3 (+ bundled tesseract.js for scans and images),
  Z3, the Red-Hat hook and the Parsure report. Nothing is mocked. Use a
  scratch schema (`ASSURE_PG_SCHEMA=bench_run`; reset with
  `DROP SCHEMA bench_run CASCADE`). `.env` is not loaded; export
  `ASSURE_S3_BUCKET` only if you want the OMP mirror to hit the real bucket.
- `PARSURE_LLM_EXTRACTION=0` by default (the local Ollama may be absent, and
  the label pass is the deterministic baseline). `--llm` turns the grounded
  LLM pass on; the results file records which.
- Each input is ingested **twice** (replay determinism); `--no-replay` skips
  the second run.
- Filters: `--class photos` (repeatable), `--case pol-auto-declarations-typed`
  (repeatable). `--baseline bench/results/<file>.json` prints per-class
  deltas, routing flips, newly missed / newly found fields and gate changes.
  `--dump-dir /tmp/bench` writes the generated inputs for inspection.
- Output: a Markdown summary on stdout and
  `bench/results/<UTC timestamp>-<git sha>.json` (gitignored; keep the ones
  that back a decision). Exit status **1 when any gate fails**, 2 when nothing
  ran or the manifest is invalid.

Unit tests for the harness and the set: `tests/test_benchmark_harness.py`
(manifest validity, every generator opens, metric arithmetic, gates, `n/a`).

## What is measured

| Metric | Definition | Source in the report |
|---|---|---|
| routing accuracy | `classification.document_type == expected`; a mixed bundle must also segment into the expected page ranges and types | `classification`, `documents[]` |
| family accuracy | the type's family (`TYPE_FAMILY`, or `<family>` of `<family>_unknown`, else the cue family) equals the expected family | `classification.validation.family` |
| extraction recall | expected fields (name → value on the page) found with the expected value: numbers within 0.005, strings case/whitespace-insensitive, code lists as sets; bundles scored per segment | `fields[]` |
| anchoring rate | found fields (value not null) that carry `field_source_node_id` (or `tree_node_id`) | `fields[].field_source_node_id` |
| Red-Hat contradiction recall | seeded cross-document conflicts (pairs differing in `policy_number` / `insured_name` / `vin`) surfaced in `conflicts[]` (`kind: cross_document`) or in `redhat.findings[]` under rule `cross_document_conflict` | `conflicts[]`, `redhat` |
| replay determinism | the multiset of `element_id` (fields + layout; falls back to `field_source_node_id` when no element ids exist) is identical across the two runs; value determinism and saved-tree node-id determinism are reported alongside | `fields[].element_id`, `_layout` |
| signature accuracy (reported, no gate) | `quality_report.signature.present` equals the case's `signature_present` | `quality_report.signature` |
| latency p50 / p95 | **end-to-end ingest** (`ingest_pdf_for_project`'s own `duration_ms`: route → parse/OCR → verify → save → report), nearest-rank percentiles per benchmark class and per latency class. The report's `timings_ms.total` is recorded but not gated — it clocks the report build only (measured 2026-09-26: 11.7 ms on a photo whose ingest took 2 447 ms) | ingest result; `report.timings_ms` |

A metric with no inputs prints `n/a` and its gate is `n/a` — never 100 %.

Inputs whose expected type has **no schema yet** (`expected.schema_exists:
false` — endorsement, cancellation notice, repair estimate, schedule of
forms in `bench-v1`) count as routing misses, as the plan intends, and are
flagged `schema-gap` in the table; the notes also give routing over existing
schemas so a classifier regression is not hidden behind a taxonomy gap.

## Gates (plan §8)

| Gate | Threshold | Scope |
|---|---|---|
| routing accuracy | ≥ 95 % | overall |
| anchoring rate | ≥ 90 % | overall |
| Red-Hat contradiction recall | ≥ 85 % | overall |
| replay determinism | 100 % | overall |
| P95 latency | policy_form < 5 s, claim_packet < 8 s, photo_signature < 10 s, mixed_bundle < 15 s | per latency class |

Thresholds live in `bench/manifest.json` (`gates`, `latency_classes`) so a
re-frozen set carries its own; the harness defaults match the plan.

## `bench-v1` measured — 2026-09-26, commit `8ad8481`, dev PostgreSQL, LLM off

`bench/results/20260926T120731Z-8ad8481.json`. 25 synthetic cases run
(28 inputs), 4 real-document placeholders skipped.

| class | inputs | routing | family | recall | anchoring | red-hat | replay | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|
| policies | 6 | 67 % (2 schema-gap) | 100 % | 95 % (53/56) | 100 % (57/57) | 100 % (1/1) | 100 % | 0.32 s | 0.49 s |
| claims | 6 | 83 % (1 schema-gap) | 100 % | 94 % (49/52) | 100 % (53/53) | 100 % (1/1) | 100 % | 0.31 s | 0.34 s |
| photos | 4 | 100 % | 100 % | 68 % (27/40) | 100 % (32/32) | n/a | 100 % | 0.74 s | 1.07 s |
| signatures | 3 | 100 % | 100 % | 94 % (31/33) | 100 % (33/33) | n/a | 100 % | 1.12 s | 1.17 s |
| handwritten | 2 | 100 % | 100 % | 50 % (6/12) | 100 % (11/11) | n/a | 100 % | 0.82 s | 0.96 s |
| mixed | 7 | 71 % (1 schema-gap) | 86 % | 79 % (58/73) | 100 % (70/70) | 0 % (0/1) | 100 % | 0.34 s | 1.56 s |
| **overall** | 28 | **82 %** (4 schema-gap; 96 % over existing schemas) | 96 % | 84 % (224/266) | **100 %** | **67 %** (2/3) | **100 %** | 0.32 s | 1.17 s |

| gate | measured | status |
|---|---|---|
| routing ≥ 95 % | 82 % | **FAIL** |
| anchoring ≥ 90 % | 100 % | PASS |
| Red-Hat recall ≥ 85 % | 67 % | **FAIL** |
| replay 100 % | 100 % (on `element_id`; saved-tree node ids 0 %) | PASS |
| P95 policy_form < 5 s | 0.49 s | PASS |
| P95 claim_packet < 8 s | 0.34 s | PASS |
| P95 photo_signature < 10 s | 1.17 s | PASS |
| P95 mixed_bundle < 15 s | 1.56 s | PASS |

Signature accuracy (no gate): 1/13 (8 %) — every `/s/` e-signature line and
the rasterised ink stroke read `present: false`, because
`quality_probe.assess_signature` judges the bottom 20 % of the page and the
benchmark pages end their signature block at ~45 % of the height. The one
hit is the blank line.

### What the failures are

- **Routing 82 %.** Four misses are schema gaps (endorsement, cancellation
  notice, repair estimate, schedule of forms → read as `auto_policy` /
  `auto_claim` / `property_policy`); the fifth is the **rotated scan**: 90°
  rotated pixels OCR to noise (`ocr_confidence 0.51`), type `uncertain`,
  0/11 fields, and because OCR "read something" Textract is never tried.
- **Recall 84 %.** Phone photo at 72 dpi: 1/11 (OCR confidence 0.66);
  110 dpi: 9/11. Handwritten (script font, 200 dpi): 3/4 on a whole-note
  hand, 3/8 when only the values are handwritten. Table cells: 0/4 in-table
  values on the coverage schedule, the estimate total under the table missed.
  Endorsement: the *removed* VIN is extracted, not the added one. Label
  regexes: `federal_tax_id` (two labels on the CMS-1500), `cause_of_loss`
  with an em dash, `vin` on two 200-dpi scans (OCR reads `VIN:` line
  imperfectly — `ocr_confidence 0.94`, value not 17 clean characters).
- **Red-Hat recall 67 %.** `policy_number` and `vin` pairs are caught by
  `field_extractor.cross_document_conflicts`. The `insured_name` pair is not:
  the claim schema carries `claimant_name`, and the conflict check compares
  `insured_name` only — a policy ↔ claim name mismatch is invisible by
  construction. No report carried a `redhat` block at ingest time
  (`services/redhat_graph.attach_findings` is not called from the ingest
  path), so recall today is entirely `conflicts[]`.
- **Replay 100 % / 0 %.** `element_id` (`eid-v1`, derived from chunk id,
  page, bbox and text) is identical across two ingests of the same bytes —
  the gate passes on the identity the fields and Red-Hat anchors use. The
  saved-tree node ids (`p-…`, `img-…`, what the Review page deep-links to)
  are freshly generated on every ingest: 0 % identical. The plan's "100 %
  once the identity agent lands" holds for element ids; node ids still move.
- **Latency.** All four ceilings pass by a wide margin on this laptop
  (text-layer PDF ≈ 0.31 s, OCR page ≈ 0.7–1.6 s end to end). One caveat
  from a single-case run: the first ingest of a fresh process measured 11.1 s
  (imports, pool, jdf-cli warm-up); the full run's first input measured
  0.49 s. Percentiles include the first input; a cold worker will show the
  former. `report.latency_class` agrees with the manifest's class on 54 % of
  inputs — the code puts scanned PDFs under `policy_form`/`claim_packet`
  (cost tracked by `parser_name`), the manifest follows the plan and puts
  scanned attachments, signature and handwriting scans under
  `photo_signature`.

### What the harness could not measure yet, and why

- **Real carrier documents** — four `s3` placeholders skipped; no real file
  has been dropped in (`bench/cases/real/<case id>/` or `ASSURE_BENCH_BUCKET`).
  Every recall number above is on synthetic pages with clean fonts; the plan's
  gate is meaningful only once the placeholders are filled.
- **Red-Hat findings at ingest** — `redhat.findings[]` is read when present;
  it was present on 0 of 3 pair cases, so recall is `conflicts[]` recall.
- **Textract path** — the scan backend was `jdf-ocr` (default; no AWS
  credentials in the run). `PARSER_SCAN_BACKEND=textract` re-runs the same
  set against Textract; not measured here.
- **Grounded LLM pass** — off. `--llm` with a reachable model gives the
  label+LLM figure; not measured here.
- **Multi-process / queued latency** — the numbers are in-process, warm, one
  document at a time; the worker's queue wait and the 202/poll round trip
  are not in them.
- **Handwriting with real hands** — a script font is an optimistic proxy; on
  a machine without one the generator marks the case
  `not_extractable_by_design` and the note says so.

## Governance

1. A case is never removed once it fails; a failing case is the record of a
   gap, and only a code change may turn it green.
2. Every new field failure found on a customer document becomes a case
   (synthetic when the wording can be reproduced, `s3` + sha256 when not)
   before the fix lands.
3. Results are versioned: `bench/results/<timestamp>-<sha>.json` per run;
   keep the files behind a launch decision (release attachment or a results
   branch); compare with `--baseline`.
4. The manifest is frozen per version: adding cases keeps `bench-v1`;
   changing a wording, a rendering parameter, an expected value or a gate is
   `bench-v2` with a new `frozen_at`, and the old manifest is kept.
5. Expected values are the document's truth, never the extractor's output.
6. Never tune the harness to pass; `n/a` is the only answer for a metric with
   no inputs.

## Adding real S3 cases

Bucket layout (placeholder — replace `${ASSURE_BENCH_BUCKET}` with the
customer's benchmark bucket; never the production upload bucket):

```
s3://${ASSURE_BENCH_BUCKET}/bench-v1/
  policies/     real_auto_declarations_001.pdf  …
  claims/       real_cms1500_scan_001.pdf       …
  photos/       real_phone_photo_001.jpg        …
  signatures/   …
  handwritten/  real_adjuster_note_001.pdf      …
  mixed/        …
```

1. Upload the file (or copy it to `bench/cases/real/<case id>/<filename>`
   for a local run — that directory must never be committed).
2. In `bench/manifest.json` fill the placeholder's `expected.document_type`,
   `expected.family`, `expected.fields` (values as written on the page; dates
   ISO `YYYY-MM-DD`, money and limits as numbers, code lists as arrays),
   `expected.signature_present` when a signature block exists, and
   `source.sha256`; remove `"status": "placeholder"`. For a new document add
   a new entry (`id` kebab-case, prefixed `s3-`).
3. `ASSURE_BENCH_BUCKET=<bucket> scripts/benchmark.py --case <id>` (boto3
   uses the ambient AWS identity — the same resolution as
   `services/aws_integration`).
4. Attach the results file to the PR that changes the manifest.
