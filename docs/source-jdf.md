# Source JDF — the document the models read, as an artifact

*2026-09-28.* `services/source_jdf.py`, `routers/documents_routes.py`, schema v36.

## Why

Until now the raw jdf-cli document — `pages[].elements[]` with millimetre
positions, the thing the extraction and verification passes actually read —
existed only in memory during a parse (`bundle["jdf_source"]` in
`services/pdf_ingest.py`, `extracted["jdf"]` in `routers/substrate.py`). The
browser rendered the Assure tree, a paragraph list with no positions, so a
reviewer could neither see the page a field came from nor point at a span of it.
The stored source JDF closes both gaps: `@uurtech/jdf` renders it
(`<jdf src="/api/projects/<pid>/documents/<doc>/source.jdf">`), and a text
selection made on it travels back to the server as an anchor.

## What is stored

After a successful jdf-cli parse (text layer or OCR — not Textract, not the
PyMuPDF fallback, not a text upload), the raw document is written as JSON to the
object store (`services/object_store.py`: S3 when `ASSURE_S3_BUCKET` is set,
else `<ASSURE_DATA_DIR>/objects/`), content type `application/json`, under

```
documents/<project_id>/<document_id>/<revision>.jdf
```

| Path | `document_id` | `<revision>` |
|---|---|---|
| Import (`POST …/import-pdf`, `services/pdf_ingest`) | the tree's id, `doc-<project_id>` | the revision id the save is about to use (`new_revision_id()` is drawn first, passed to `save_jdf_revision`) |
| Sources panel, queued (`SUBSTRATE_ASYNC_UPLOAD=1`) | the vault row id (`substrate_files.id`, the `document_id` the intake report carries for this path) | the ingest job id |
| Sources panel, synchronous | same | `sha-<sha256(bytes)[:16]>` — no job row exists; the same bytes land on the same key |

The project is the first key segment so a key can be checked against the
requesting project (`key_in_project`), as `key_belongs_to_project` checks
upload keys. A document id alone is a client string.

The stored document is jdf-cli's, plus two additions:

* `meta.assure` — `{project_id, document_id, revision, filename, stored_at,
  element_id_policy: "eid-v1", element_id_derivation, elements_stamped,
  rasters}` (`rasters` since 2026-09-28: how many pages carry a page raster
  element, see below).
* `element.assure.element_id` on every text element — the `eid-v1` id
  (`field_extractor.derive_element_id`), derived through
  `field_extractor.page_layout` with the parse's chunk list, i.e. exactly the id
  the Parsure fields carry (`field.element_id`, `source_span.element_id`).
  jdf-cli 0.2.3 elements have no id and the chunk list is not part of the
  document, so without the stamp a reader could not reproduce the field ids.
  `tests/test_source_jdf.py::test_element_ids_equal_the_parsure_field_ids_on_the_same_bundle`
  proves the equality on one bundle.

The in-memory bundle is deep-copied before stamping; the pipeline keeps reading
jdf-cli's document as produced.

For an OCR document the stored pages also carry a page raster element — see
"Page rasters" below. A digital PDF's stored document is jdf-cli's plus the two
additions above and nothing else.

## Where the key is recorded

* `ingest_jobs.source_jdf_key` (migration v36, `ALTER TABLE … ADD COLUMN`,
  guarded by `_column_exists`). Written by
  `ingest_jobs_repository.set_source_jdf_key`, its own statement — not an
  `advance()` field, because storing the document is not a stage transition
  and a finished job may still receive it.
* The Parsure report: `report["source_jdf"]` — the descriptor below, or `null`
  when nothing was stored. The pipeline puts the descriptor on
  `intake["source_jdf"]`; `v1_orchestrator.build_report` copies it (the key is
  in `CONSUMED_INTAKE_KEYS`, so it does not also appear in `intake_extra`).
* The Assure tree saved by the import path: `meta.source_jdf`.
* The route payloads: `import-pdf` and `substrate/upload` answer with
  `source_jdf` (or `null`).

Descriptor:

```json
{"key": "documents/p1/doc-p1/rev-….jdf",
 "url": "/api/projects/p1/documents/doc-p1/source.jdf",
 "pages": 12, "elements": 431,
 "stored_at": "2026-09-28T10:00:00Z", "revision": "rev-…", "document_id": "doc-p1", "bytes": 88211}
```

## Routes

Both require `projects.read` and project ownership. The web tier streams bytes
and reads JSON it already stored; nothing is parsed here.

`GET /api/projects/<pid>/documents/<doc>/source.jdf[?revision=<rev>]`
: The stored JSON. `Content-Type: application/json`, `Cache-Control: private,
  max-age=3600`, `ETag: "<key>"` (a stored revision never changes under its key;
  `If-None-Match` answers 304), `X-Source-Jdf-Key`. Latest by default —
  resolved from the project's ingest jobs (`source_jdf_key`, `created_at`
  descending, `revision_version` breaking same-second ties), then from the
  project's intake reports (`report.source_jdf.key`) for a synchronous Sources
  upload that has no job. `?revision=` must name a stored revision or the
  answer is 404. Every candidate key is checked against the project prefix.
  404 body: `{"ok": false, "error": "no source document is stored for this document"}`.

`GET …/source.json`
: `{ok, url, key, pages, elements, rasters, stored_at, revision, element_id_policy}`.

`GET …/documents/<doc>/pages/<n>.png[?revision=<rev>]`
: The page raster the worker stored for page `n` (1-based) of an OCR document
  (next section). `Content-Type: image/png`, `Cache-Control: private,
  max-age=3600`, `ETag: "<raster key>"` (304 on `If-None-Match`),
  `X-Source-Jdf-Key`. The document is resolved exactly as `source.jdf` is, and
  the raster key is derived from that key (`page_raster_key`), so a raster is
  only ever served for the requesting project's own document and revision.
  404 `{"ok": false, "error": "no page raster is stored for this page"}` for a
  digital PDF, a page that did not render, or a page past the end; `n < 1` is
  400. Nothing is rendered on the web tier to answer a miss.

`GET …/source.json?text=<selection>[&page=<n>]`
: `{ok, key, url, element_ids, page, bbox_rel, found, matched_by}`. The page
  text is the elements joined by newlines (`page_layout`'s own page text). The
  selection is looked up verbatim first (`llm_extraction.find_verbatim`:
  whitespace-collapsed, case-insensitive → `matched_by: "verbatim"`), then under
  `source_jdf.find_normalised` → `matched_by: "normalised"`, which additionally
  removes soft hyphens, joins the line-wrap hyphen (`insur-\nance` / `insur- ance`
  → `insurance`: a hyphen after a letter, followed by whitespace and a
  lower-case letter — `Policy - Auto` and `X-\nRay` are left alone), expands the
  ligatures ﬀ ﬁ ﬂ ﬃ ﬄ ﬅ ﬆ, straightens curly quotes/apostrophes and dashes, and
  makes non-breaking/thin spaces plain. Both sides are normalised; the match is
  an exact substring of the normalised page text — no edit distance, no token
  overlap. `element_ids` and `bbox_rel` are exact (the offsets map back to the
  page text; the boxes are the stored positions), so the UI can show that a
  repair happened without the repair moving anything. Without `page` the pages
  are searched in order. Not found: `found: false`, `element_ids: []`,
  `bbox_rel: null`, `matched_by: null` — never the nearest element. `text`
  longer than 4000 characters or a non-integer `page` is 400.

## Page rasters (OCR documents, 2026-09-28)

**Why.** A scanned or photographed page has only OCR text in its source JDF, so
jdf.js drew the OCR's reading of the page and not the page: no signature, stamp,
handwriting or layout a reviewer could check the reading against. Round 5's
signature taxonomy and the handwriting gap (`docs/parsure.md`) both need the page
itself in front of the reviewer.

**When.** `source_jdf.pages_are_ocr(parser_name, modality=, source_kind=)`: the
parser name carries an OCR engine (`jdf-cli+tesseract`, `jdf-cli+openai`,
`textract`), or the router's modality is `scanned_pdf` / `phone_photo` /
`screenshot`, or the source kind is `scanned` / `photo` / `image`. A digital PDF
(`jdf-cli`, `digital_pdf`, `pdf`) is unchanged — its text layer is the page. On
the import path (`services/pdf_ingest`) the parser name and the router's
modality decide; on the Sources path (`routers/substrate.ingest_substrate_file`)
the parser name and the source kind, because that path computes the router's
intake dict later. Rasters are stored only when a source JDF is stored: a
Textract document has no jdf-cli document to put the element into, so it gets
neither (the route answers 404 for it as before).

**What.** `store_page_rasters` renders every page once, on the worker, with the
same PyMuPDF render the visual probe and the vision pass use
(`services/vision.render_page_png` → `quality_probe._open_document`): PNG, long
side ≤ `RASTER_LONG_SIDE_PX` = 1600 px (~190 dpi on a letter page; an uploaded
image is rendered at its native pixels and never upscaled), at most
`RASTER_MAX_PAGES` = 200 pages, into the object store under

```
documents/<project_id>/<document_id>/<revision>/pages/<n>.png
```

beside the `.jdf` of the same revision. Measured 2026-09-28 on the compose
stack: a 595×842 pt page rendered from `final_run_debris.pdf` → 1109×1568 px,
100 KB PNG. The original upload bytes are rendered, not the PDF the image was
wrapped in for jdf-cli.

`persist_source_jdf(..., rasters=)` then puts one `image` element **first** on
each rastered page (`add_page_rasters`), so the text elements paint over it:

```jsonc
{"type": "image",
 "src": "/api/projects/<pid>/documents/<doc>/pages/<n>.png",
 "alt": "Page <n> as scanned",
 "position": {"x": 0, "y": 0},
 "width": <pageSize.width mm>, "height": <pageSize.height mm>,
 "fit": "contain",
 "assure": {"kind": "page_raster", "page": <n>, "key": "<raster key>", "width_px": 1109, "height_px": 1568}}
```

`width`/`height` follow jdf.js's own fallback (`page.pageSize`, else
`meta.pageSize`, else A4; a named size such as `"Letter"` is resolved to
millimetres), so the box is exactly the page jdf.js lays out; jdf.js draws a
`src` that starts with `/` or `http` as-is and letter-boxes the bitmap (`fit:
contain`). The element carries no text, so `page_layout`, the element index,
the stamped ids and `?text=` lookups skip it; `elements` on the descriptor still
counts text elements only.

**Recorded.** `descriptor["rasters"]` (import payload, `ingest_jobs` via the
descriptor on the report, `report.source_jdf.rasters`, `tree.meta.source_jdf.
rasters`, `source.json`) is the number of pages that carry a raster element —
the pages the reader can actually see. A page that did not render is logged
and absent: no element, not counted, its route 404. `meta.assure.rasters` on the
stored document says the same.

**Not claimed.** A raster is the page the worker rendered from the uploaded
bytes; none is synthesised, none is rendered on request, and a document with
`rasters: 0` shows the OCR text alone as before. The raster is a picture of the
page, not evidence of anything on it: no field, claim or verdict reads it.

## Selection anchors on the compile and inquire streams

`POST …/draft/stream` and `POST …/inquire/stream` accept an optional

```json
"selection": {"text": "…", "page": 3, "element_ids": ["c7:1a2b3c4d5e6f"],
              "document_id": "doc-p1", "source_jdf": "/api/projects/p1/documents/doc-p1/source.jdf"}
```

validated strictly (`services/source_jdf.SelectionAnchor`: `text` 1–4000
characters, `page` an int or absent, `element_ids` a list of strings; a
mistyped value is 400, not coerced). With `compileType: "selection"` the
selection text is the excerpt when `content` is absent.

The produced document (`meta.selection_anchor`, set beside `meta.answer_shape`
in `routers/draft.py`) and the rewritten node (`node.meta.selection_anchor`,
set before the persist in `routers/inquire_stream.py`) carry the dict plus

* `verbatim` — true only when the text was re-found (`find_verbatim`) in the
  `extracted_text` of the cited sources: the ask's substrate rows for a
  compile, the project's Sources rows (`_substrate_rows`) for a rewrite;
* `checked_against` — how many source texts were searched.

No verdict logic reads the anchor.

**Cache.** `_compile_cache_key` appends `[selection:<sha256 of the selection
text>]` when a selection is present, so two asks that differ only in the
selected text never share an entry; a compile with no selection composes the key
it always did (a warm compile stays warm). On a replay
(`routers/draft._refresh_replayed_meta`, before `_recount_cached_verified`) the
document in both the `compiled` and the `verified` frames gets
`meta.selection_anchor` rewritten from the current request (verbatim re-checked
against this ask's sources) or removed when the request has none, and
`meta.source_jdf` / `meta.source_jdfs` resolved again. The stored entry is not
modified.

## `meta.source_jdf` on a compiled document

`routers/draft.py` sets, beside `meta.answer_shape`:

* `meta.source_jdf` — the descriptor of the first (primary, ranked) source of the
  ask that has a stored source JDF, or `null` when none has;
* `meta.source_jdfs` — the list of all such descriptors, present only when there
  are two or more.

Resolution is `services/source_jdf.descriptors_for_rows(project_id, rows)` →
`descriptor_for_document(project_id, row["id"])`: the key from
`resolve_source_jdf_key` (ingest jobs, then reports), the descriptor from the
intake report that recorded that key, else from the stored document's own
`meta.assure`. A row with nothing stored is skipped; nothing is invented. The
Sources upload stores under the vault row id, which is the row `id` the compile
receives, so the lookup is by that id.

## Tests

`tests/test_source_jdf.py`: persistence on both paths (local object store under
`ASSURE_DATA_DIR/objects`), key on job / report / tree meta, streaming with
ETag/304, `?revision=`, latest-wins, 404, cross-project refusal (including a
job row in another project pointing at the key), element-id equality with
`page_layout` and with the Parsure fields, round trip without the chunk list,
text lookup (verbatim and each normalisation, plus the refusals: misspellings,
a different figure, a non-wrap hyphen), `matched_by` on the route,
`descriptor_for_document`, `meta.source_jdf`/`source_jdfs` on a compile, the
cache key per selection, a replay carrying the request's selection and dropping
a stale one, selection passthrough on both streams and their 400s.

`tests/test_page_rasters.py`: the OCR decision, the raster key beside the source
key, an OCR import storing a ≤1600 px PNG per page with the image element first
(full page, `fit: contain`, `assure.kind: page_raster`) and `rasters` on the
payload / report / `source.json`, the route (PNG, ETag/304, private cache,
`?revision=`, 404 for a missing page or another project, 400 for page 0), a
digital PDF storing none and answering 404, a page that fails to render being
absent and uncounted, idempotent element insertion, named page sizes, and the
Sources upload path.

Note for test authors: pin `ASSURE_S3_BUCKET` to `""` rather than `delenv` —
`cloud_billing` runs `load_dotenv(override=False)` on app import and restores a
deleted variable from the developer's `.env` (one test write reached the real
bucket before this was understood, 2026-09-28; the object was removed).
