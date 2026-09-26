# Parsure benchmark set — `bench-v1`

Frozen 2026-09-26 at commit `8ad8481`. Run with `scripts/benchmark.py`
(see `docs/benchmark.md` for the gates and `docs/corpus.md` for the corpus
taxonomy the cases cover).

```
bench/
  manifest.json     the frozen set: one entry per case (id, class, family,
                    latency class, inputs with expected type/fields, seeded
                    contradictions, source)
  cases/
    texts.py        the document wordings (frozen with the manifest)
    generators.py   PyMuPDF generators named by the manifest
    real/<case id>/ drop-in directory for real carrier documents (gitignored
                    by the team's policy; never commit customer paper)
  results/          one JSON per run: <UTC timestamp>-<git sha>.json (gitignored)
```

## Classes

`policies`, `claims`, `photos`, `signatures`, `handwritten`, `mixed` — the
six classes of the customer's benchmark plan (§3, §8). Every class has at
least two synthetic cases; four `s3` placeholders wait for real documents.

## Governance rules

1. **A case is never removed once it fails.** A failing case is the record
   of a gap; fixing the code is the only way to turn it green. Rewording a
   case to make it pass is a new benchmark version, not a fix.
2. **Every new field failure becomes a case.** When a customer document
   surfaces a miss (wrong type, missed field, lost anchor, non-deterministic
   replay), add a case that reproduces it — synthetic if the wording can be
   reproduced, `s3` with a sha256 if it cannot — before the fix lands.
3. **Results are versioned.** Every run writes
   `bench/results/<timestamp>-<sha>.json`; the team keeps the files that
   back a launch decision (attach them to the release, or check them into a
   results branch). `--baseline <file>` diffs a run against any earlier one.
4. **The manifest is frozen per version.** Adding cases bumps nothing (the
   set only grows); changing an expected value, a generator parameter or a
   wording bumps `version` (`bench-v2`) and `frozen_at`, and the old manifest
   is kept as `manifest-bench-v1.json`.
5. **Expected values are the document's truth, not the extractor's output.**
   An endorsement's VIN is the *added* vehicle; a torn page expects only the
   fields above the tear; a handwritten note expects its fields even when
   OCR cannot read the hand. Cases whose expected type has no schema yet carry
   `schema_exists: false` and are reported as schema gaps, still counted as
   routing misses.
6. **Never tune the harness to pass.** Thresholds live in the manifest
   (`gates`, `latency_classes`) and in the plan; a metric with no cases prints
   `n/a`, never 100 %.

## Adding a real document

1. Put the file under `bench/cases/real/<case id>/<filename>` (or upload it
   to `s3://<bench bucket>/bench-v1/<class>/<filename>` and set
   `ASSURE_BENCH_BUCKET`).
2. Fill the case's `expected.document_type`, `expected.family`,
   `expected.fields` (name → value as written on the page, dates ISO,
   money as numbers) and `source.sha256`; remove `"status": "placeholder"`.
3. Run `scripts/benchmark.py --case <case id>`; commit the manifest change
   with the run's results file attached to the PR.
