# Parse-path runtime: what ships now, and what waits

Status: Option 3 in effect. `pymupdf` and the `jdf` CLI ship in the app image;
Docling does not. The worker split (Option 2) is designed but not deployed, and
`Dockerfile.worker` sits unused until a worker exists to run it.

Written 2026-09-20, after the staging convergence (`448b56c`, `719ade4`).

## The problem this document answers

Three upload endpoints existed and all three failed in the deployed container,
for a missing runtime rather than a missing feature.

| Endpoint | Converter | Needed | Was |
|---|---|---|---|
| `POST /api/projects/<id>/import-pdf` | `services/pdf_import.pdf_bytes_to_jdf` | PyMuPDF (`fitz`) | absent, not in requirements.txt |
| `POST /api/projects/<id>/jdf/ingest` | `services/jdf_converter` → `jdf` CLI | Node + `@uurtech/jdf-cli` | absent from the image; box had them, container did not |
| `POST /api/projects/<id>/substrate/upload` | `routers/substrate.extract_document_text` | Docling (or Textract) | `USE_DOCLING=1` set while requirements.txt kept `docling` commented out |

`jdf_converter` resolves its binary as
`shutil.which("jdf") or "/opt/node-v24.11.1-linux-arm64/bin/jdf"`, and the `jdf`
shim is `#!/usr/bin/env node`. The runtime stage carried neither, so that path
could not start even though the box's own Node install works.

## Measured before choosing

On the staging box, against real insurance PDFs
(`/home/ubuntu/real-docs/`, 10-page `cp10300917-sample.pdf`):

| Operation | Time |
|---|---|
| `jdf convert` (PDF → JDF) | 1.93 s |
| `jdf chunk` (JDF → 20 section chunks) | 0.15 s |
| OMP `:3456` `/v1/health` round-trip | 18 ms |

Output quality from `jdf convert`: 10 pages, 48 elements on page 1 (richtext,
shape, table, text), 4 tables on page 1 carrying `columns`, `rows`, `style`,
`borders`. Chunks carry `id`, `text`, `path`, `page`, `types`, `tokens`, `hash`.

Conclusion: the cost is parsing (~2 s), not the artifact layer (18 ms). OMP is
not the bottleneck and does not need to be kept off the request path for speed
reasons; the parse is what needs to move.

## The options, and why 3

### Option 1 — Docling inline in the app image

Simplest and matches how staging actually runs today (synchronous — see below).
Rejected because `pip install docling` pulls a CUDA toolkit and NVIDIA libraries.
Observed during an attempted build on ARM64 with no GPU:

    Installing collected packages: ... nvidia-cusparselt-cu13, cuda-toolkit,
    triton, nvidia-nvtx, nvidia-nvshmem-cu13, nvidia-nvjitlink, nvidia-nccl-cu13,
    nvidia-curand, nvidia-cufile, ...

The box's own working venv confirms this is Docling's normal install shape: it
carries `cuda-toolkit 13.0.3.0`, `nvidia-cudnn-cu13 9.24.0.43`,
`cuda-bindings 13.4.2` alongside `docling 2.129.0` and `docling-slim 2.129.0`.
That payload is unusable on the target hardware and would ride in every request
worker. Rejected on the "no fat image" constraint.

### Option 2 — Split images, with a worker that runs the parse

The intended end state. `Dockerfile.worker` builds from the app image and adds
only what `requirements-worker.txt` declares (Docling, pypdfium2), where
`requirements-worker.txt` is:

    docling==2.129.0
    pypdfium2==5.13.0

and `docker-compose.celery.yml` takes `WORKER_IMAGE` instead of building from
the app Dockerfile.

**Not deployed, because it has no deployment target today.** Measured on
staging:

| Component | State |
|---|---|
| `SUBSTRATE_ASYNC_UPLOAD` | absent → defaults `0` → parsing is synchronous |
| `CELERY_BROKER_URL` | absent |
| Celery worker process | not running |
| Celery systemd unit | none |
| Containers on the box | `assure-assure-app-1` only |
| `docker-compose.celery.yml` | referenced by no staging workflow |

`worker-staging.yml` deploys a Cloudflare Worker from `worker/` and is unrelated
to Celery. So building the worker image today would produce an artifact nothing
runs. Option 2 is deferred until the async path is real, and it is a deliberate
piece of work: SQS or SQLite broker, a supervised container, compose wiring.

### Option 3 — Ship the cheap parsers, defer Docling (chosen)

`pymupdf` and Node + `@uurtech/jdf-cli` in the app image. Both cover the
born-digital case, which is what `/home/ubuntu/real-docs/` contains, at ~2 s per
document. The app image grows by roughly 20 MB plus the Node runtime rather than
gigabytes.

`USE_DOCLING=0` in the app image, because the app must not claim a branch it
cannot take. The value moves to the worker image, where Docling is present.

## Consequences to remember

**The substrate upload path needs credentials, not a package.** With
`USE_DOCLING=0`, `extract_document_text` falls back to Textract. The container
carries neither `AWS_REGION` nor AWS credentials (`printenv` on the live
container lists neither), so that fallback is unreachable until the environment
is wired. Docling in the worker is the alternative once the worker exists.

**Docling's unique value is the case Option 3 does not cover.** Scanned
documents and handwriting need layout inference or OCR; a geometry parse reads
text operators and cannot invent them. AWS Textract does return handwriting
(`detect_document_text` marks blocks `TextType: "HANDWRITING"`) and
`lib/textract.py` already calls it, so the capability exists in code — it is
credentialing, not development.

**JDF and Docling are not substitutes for each other, but the split is clean.**
`jdf` is a geometry parser: `packages/jdf-pdf-import` depends on `pdfjs-dist`,
with no model weights, fully deterministic. Docling runs neural layout models.
Born-digital → `jdf`; scanned or handwritten → Docling/Textract.

**Memos is a mirror, never a dependency.** It has no relations between memos, so
it cannot express the lineage the artifact layer requires
(`parent_of`/`child_of`/`derived_from`/`references`/`verifies` in
`models/omp.py`). Its only coherent role is an asynchronous, human-browsable
projection of artifacts that OMP already holds. No request may wait on Memos,
and no compile may read from it.

## What to do when picking this up

1. Deploy a Celery worker and set `SUBSTRATE_ASYNC_UPLOAD=1`, so parsing leaves
   the request path.
2. Build and publish `Dockerfile.worker`; point `WORKER_IMAGE` at it.
3. Set `USE_DOCLING=1` on the worker only, and verify a scanned PDF through the
   substrate path.
4. Pass `AWS_REGION` and Textract credentials into the app container, or drop the
   fallback and route scanned input to the worker explicitly.
5. Only then add the Memos mirror, asynchronously, after the parse artifact is
   written.
