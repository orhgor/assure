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
  element_id_policy: "eid-v1", element_id_derivation, elements_stamped}`.
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

No page images are rendered or stored. `services/vision.render_page_png` renders
one page for the multimodal read on demand and keeps nothing; adding a render
step to the ingest was out of scope.

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
: `{ok, url, key, pages, elements, stored_at, revision, element_id_policy}`.

`GET …/source.json?text=<selection>[&page=<n>]`
: `{ok, key, url, element_ids, page, bbox_rel, found}`. The page text is the
  elements joined by newlines (`page_layout`'s own page text); the selection is
  found verbatim under `llm_extraction.find_verbatim` (whitespace-collapsed,
  case-insensitive); the elements whose character range overlaps the match are
  the answer and `bbox_rel` is the union of their relative boxes
  `[x0, y0, x1, y1]` in page fractions. Without `page` the pages are searched in
  order. Not found: `found: false`, `element_ids: []`, `bbox_rel: null` — never
  the nearest element. `text` longer than 4000 characters or a non-integer
  `page` is 400.

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

No verdict logic reads the anchor. A compile replayed from the OMP cache yields
the cached document; the anchor is a fact of the compile that produced it, so a
replay for a different selection of the same excerpt text carries the earlier
anchor (`PEM_OMP_CACHE`, off under test).

## Tests

`tests/test_source_jdf.py`: persistence on both paths (local object store under
`ASSURE_DATA_DIR/objects`), key on job / report / tree meta, streaming with
ETag/304, `?revision=`, latest-wins, 404, cross-project refusal (including a
job row in another project pointing at the key), element-id equality with
`page_layout` and with the Parsure fields, round trip without the chunk list,
text lookup, selection passthrough on both streams and their 400s.

Note for test authors: pin `ASSURE_S3_BUCKET` to `""` rather than `delenv` —
`cloud_billing` runs `load_dotenv(override=False)` on app import and restores a
deleted variable from the developer's `.env` (one test write reached the real
bucket before this was understood, 2026-09-28; the object was removed).
