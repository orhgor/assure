"""Claude Opus 5.5 on Amazon Bedrock as the parser (``PARSER_BACKEND=bedrock``).

User decision 2026-10-01: OpenRouter is gone; the uploaded file goes to Claude
Opus 5.5 on Bedrock, which reads it the way a person would — printed text,
handwriting, ticked boxes, stamps, signatures — and every field it states is
written out. The reading is saved as a JDF document so everything downstream
(layout, table pass, raw candidates, source view, page rasters) continues
exactly as after Textract or jdf-cli.

How: every PDF page is rendered (``PAGE_LONG_SIDE_PX``, JPEG) and sent as an
``image`` block, an image upload as itself, through
``bedrock-runtime.invoke_model`` (the Messages API body). Until 2026-10-01 the
PDF went as a ``document`` block; on EC2 a scanned PDF then came back without
its fields while the same file given to Opus directly was read in full — the
model must see the page picture, not a text extraction of it. A PDF longer
than ``PAGES_PER_REQUEST`` is split into page ranges that are read in parallel; an answer cut off at the
output ceiling (``stop_reason: max_tokens``) is re-read in halves rather than
"repaired" into a document with its last pages missing. The model's pages are
renumbered 1..N in reading order (its own numbers are kept as metadata) so
JDF ids, page rasters and form pairs always agree on which page is which.

The answer is translated into Textract-style blocks and written by
``services/textract_jdf.build_jdf`` into the same JDF shape Textract
produces: one full-page ``image`` element per page whose ``ocr.blocks`` are
the lines (``handwritten`` / ``illegible`` flags carried), ``table`` elements,
``bundle.forms``.

What is *not* claimed: a model gives no measured read confidence, so
``ocr_confidence`` is ``None``; boxes are the model's estimate
(``bbox_source: model_estimate``) — good enough to point at the region, not a
measurement. Text the model marks illegible is written as ``[illegible]`` and
flagged, never guessed into a confident value. Every request is booked to the
ledger under the ``parse`` stage with its token usage.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

log = logging.getLogger(__name__)

try:
    from . import model_calls as _mc
    from . import textract_jdf as _tj
except ImportError:  # pragma: no cover - flat-import fallback
    import model_calls as _mc  # type: ignore
    import textract_jdf as _tj  # type: ignore


class LLMParseError(Exception):
    """The model path could not read the document (config, transport, or no
    usable JSON). Callers report it as a failed ingest; nothing is fabricated."""


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


MAX_OUTPUT_TOKENS = _env_int("ASSURE_LLM_PARSE_MAX_TOKENS", 32000)
TIMEOUT_S = float(os.environ.get("ASSURE_LLM_PARSE_TIMEOUT_S", "600") or 600)
MAX_PAGES = _env_int("ASSURE_MAX_PAGES", 50)
#: Pages per request. A dense form page is 1.5–3k output tokens of lines +
#: boxes + pairs; 8 pages stay well under the 32k ceiling and read in ~1 min.
PAGES_PER_REQUEST = _env_int("ASSURE_LLM_PARSE_PAGES_PER_REQUEST", 8)
CONCURRENCY = _env_int("ASSURE_LLM_PARSE_CONCURRENCY", 4)
#: Anthropic image limits: ≤ 5 MB encoded, ≤ 8000 px a side; 2400 px on the
#: long side keeps handwriting legible and the request small.
IMAGE_LONG_SIDE_PX = 2400
IMAGE_MAX_BYTES = 3_750_000
PROMPT_VERSION = "llm-parse-v4-page-images"
#: Long side of a PDF page rendered for the model. 2000 px keeps every page
#: under the per-image limit that applies once a request carries many images,
#: and a JPEG at that size is ~0.3–0.8 MB, so 8 pages fit one request body.
PAGE_LONG_SIDE_PX = 2000

PROMPT = """You are reading a business document (insurance form, claim, policy, invoice, medical form, report) exactly as a careful human data-entry clerk would. The document may mix printed text, HANDWRITING, ticked/crossed boxes, stamps and signatures. Read every page completely; do not stop early.

Return ONE JSON object and nothing else (no prose, no code fence):
{"pages": [
  {"page": <1-based page number within the file you were given>,
   "lines": [ {"text": "<verbatim>", "bbox": [x0, y0, x1, y1], "handwritten": true|false, "illegible": true|false} ],
   "tables": [ {"headers": ["..."], "rows": [["..."]], "bbox": [x0, y0, x1, y1]} ],
   "key_values": [ {"key": "<label as printed>", "value": "<value as written>", "key_bbox": [x0, y0, x1, y1], "bbox": [x0, y0, x1, y1],
                    "handwritten": true|false, "illegible": true|false, "kind": "text"|"checkbox"|"signature"|"date"|"amount"} ]
  }
]}

Rules:
- bbox: fractions of the page width/height, 0 to 1, origin top-left, [left, top, right, bottom]. Estimate tightly around the text itself. For key_values, "bbox" is the VALUE's box and "key_bbox" is the label's box.
- lines: every line of text in reading order (printed and handwritten), copied character for character. Set "handwritten": true for handwritten lines.
- key_values: EVERY labelled field on the page — form boxes, "Label: value" pairs, table-like label/value grids, and fields filled in by hand. The key is the printed label exactly as printed; the value is what is filled in (typed or handwritten). A blank field has value "".
- Handwriting: transcribe it as written, including numbers, dates, names, amounts and codes. Do not correct spelling, do not normalise dates or amounts, do not translate. If a word or character cannot be read with confidence, write [illegible] in its place and set "illegible": true — never guess.
- Checkboxes and radio options: kind "checkbox"; the key is the question/label, the value is the option(s) that are ticked/crossed/circled, e.g. "[X] Yes". If none is marked, value "".
- Signatures: kind "signature", value "[signed]" when a signature is present, "" when the signature line is empty. Stamps: a line with the stamp text.
- Do not add fields that are not on the page, do not summarise, infer, compute or merge pages.
"""

_IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
_CONVERT_EXT = (".tif", ".tiff", ".bmp")


def parse_model() -> str:
    """The litellm-style id of the document-read model: ``bedrock/<profile>``
    for Opus 5.5 (``ASSURE_BEDROCK_MODEL_PARSE``)."""
    try:
        from .. import cost_governance as cg
    except ImportError:  # pragma: no cover
        import cost_governance as cg  # type: ignore
    return cg.bedrock_model("parse")


def _bare(model: str) -> str:
    return model.split("/", 1)[-1] if model.startswith("bedrock/") else model


def _ext(filename: str | None) -> str:
    return os.path.splitext(str(filename or "").lower())[1]


def _is_pdf(filename: str | None, data: bytes) -> bool:
    return _ext(filename) == ".pdf" or bytes(data or b"")[:4] == b"%PDF"


def parse_answer(text: str) -> dict[str, Any] | None:
    """The JSON object in a model answer (code fences and prose around it
    tolerated), or None. A truncated answer is NOT repaired here: the caller
    re-reads it in smaller ranges (see ``_read_range``)."""
    if not isinstance(text, str) or not text.strip():
        return None
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.M)
    start, end = body.find("{"), body.rfind("}")
    candidates = [body] + ([body[start:end + 1]] if 0 <= start < end else [])
    for cand in candidates:
        try:
            data = json.loads(cand)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict):
            return data
    return None


def _box(b: Any) -> dict[str, Any] | None:
    """A ``[x0, y0, x1, y1]`` box → Textract-style BoundingBox (fractions).

    The prompt asks for fractions, but a model sometimes answers on a 0–1000
    grid or in percent; a box whose largest value is above 1 is scaled by the
    smallest of 100 / 1000 that brings it into range (the review of 2026-10-01
    found such boxes clamped to a zero-size box and every highlight lost)."""
    try:
        vals = [float(v) for v in b]
    except (TypeError, ValueError):
        return None
    if len(vals) != 4:
        return None
    top = max(vals)
    if top > 1.0:
        scale = 100.0 if top <= 100.0 else 1000.0 if top <= 1000.0 else None
        if scale is None:
            return None
        vals = [v / scale for v in vals]
    x0, y0, x1, y1 = vals
    x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
    y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    return {"BoundingBox": {"Left": x0, "Top": y0, "Width": x1 - x0, "Height": y1 - y0}}


def _text_type(entry: dict[str, Any]) -> str:
    return "HANDWRITING" if entry.get("handwritten") is True else "PRINTED"


def answer_to_blocks(data: dict[str, Any]) -> list[dict[str, Any]]:
    """One page's answer as Textract-style blocks so ``textract_jdf.build_jdf``
    writes the same JDF for both readers: LINE (no Confidence — none was
    measured; ``TextType`` HANDWRITING/PRINTED and ``Illegible`` carried),
    TABLE/CELL/WORD, KEY_VALUE_SET/WORD."""
    blocks: list[dict[str, Any]] = []
    n = 0

    def _id(prefix: str) -> str:
        nonlocal n
        n += 1
        return f"{prefix}-{n}"

    for line in data.get("lines") or []:
        if not isinstance(line, dict):
            continue
        text = str(line.get("text") or "").strip()
        if not text:
            continue
        b: dict[str, Any] = {"Id": _id("line"), "BlockType": "LINE", "Text": text, "TextType": _text_type(line)}
        if line.get("illegible") is True or "[illegible]" in text:
            b["Illegible"] = True
        geo = _box(line.get("bbox"))
        if geo:
            b["Geometry"] = geo
        blocks.append(b)
    for table in data.get("tables") or []:
        if not isinstance(table, dict):
            continue
        headers = [str(h) for h in (table.get("headers") or [])]
        rows = [[str(c) for c in r] for r in (table.get("rows") or []) if isinstance(r, (list, tuple))]
        grid = ([headers] if headers else []) + rows
        if not grid:
            continue
        cell_ids: list[str] = []
        for r_i, row in enumerate(grid, start=1):
            for c_i, cell in enumerate(row, start=1):
                wid = _id("word")
                blocks.append({"Id": wid, "BlockType": "WORD", "Text": str(cell)})
                cid = _id("cell")
                cell_ids.append(cid)
                blocks.append({"Id": cid, "BlockType": "CELL", "RowIndex": r_i, "ColumnIndex": c_i,
                               "Relationships": [{"Type": "CHILD", "Ids": [wid]}]})
        tb: dict[str, Any] = {"Id": _id("table"), "BlockType": "TABLE", "Relationships": [{"Type": "CHILD", "Ids": cell_ids}]}
        geo = _box(table.get("bbox"))
        if geo:
            tb["Geometry"] = geo
        blocks.append(tb)
    for kv in data.get("key_values") or []:
        if not isinstance(kv, dict):
            continue
        key = str(kv.get("key") or "").strip()
        if not key:
            continue
        value = str(kv.get("value") or "").strip()
        kw = _id("word")
        blocks.append({"Id": kw, "BlockType": "WORD", "Text": key})
        vid = _id("kv")
        v_children = []
        if value:
            vw = _id("word")
            blocks.append({"Id": vw, "BlockType": "WORD", "Text": value, "TextType": _text_type(kv)})
            v_children = [vw]
        vb: dict[str, Any] = {"Id": vid, "BlockType": "KEY_VALUE_SET", "EntityTypes": ["VALUE"],
                              "Relationships": [{"Type": "CHILD", "Ids": v_children}] if v_children else [],
                              "TextType": _text_type(kv)}
        if kv.get("illegible") is True or "[illegible]" in value:
            vb["Illegible"] = True
        if kv.get("kind"):
            vb["Kind"] = str(kv.get("kind"))
        geo = _box(kv.get("bbox"))
        if geo:
            vb["Geometry"] = geo
        blocks.append(vb)
        kb: dict[str, Any] = {"Id": _id("kv"), "BlockType": "KEY_VALUE_SET", "EntityTypes": ["KEY"],
                              "Relationships": [{"Type": "CHILD", "Ids": [kw]}, {"Type": "VALUE", "Ids": [vid]}]}
        kgeo = _box(kv.get("key_bbox")) or geo
        if kgeo:
            kb["Geometry"] = kgeo
        blocks.append(kb)
    return blocks


# --------------------------------------------------------------------------
# File preparation: every PDF page is rendered and sent as an image
# --------------------------------------------------------------------------

def pdf_page_count(file_bytes: bytes) -> int:
    """Pages of a PDF (PyMuPDF), 0 when it does not open."""
    try:
        import fitz
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            return int(doc.page_count)
    except Exception:  # noqa: BLE001
        return 0


def pdf_slice(file_bytes: bytes, first: int, last: int) -> bytes:
    """Pages ``first..last`` (1-based, inclusive) as a new PDF."""
    import fitz
    with fitz.open(stream=file_bytes, filetype="pdf") as src:
        out = fitz.open()
        try:
            out.insert_pdf(src, from_page=first - 1, to_page=last - 1)
            return out.tobytes(garbage=3, deflate=True)
        finally:
            out.close()


def pdf_page_images(file_bytes: bytes, first: int, last: int) -> list[tuple[bytes, str]]:
    """Pages ``first..last`` (1-based, inclusive) rendered as JPEG for the model.

    Why (user finding 2026-10-01 on EC2): a scanned PDF sent as a ``document``
    block came back without its fields while the same file given to Opus 5.5
    directly was read in full — on Bedrock the document block can reach the
    model as extracted text only, and a scan has none. A rendered page is the
    picture itself, so printed text, handwriting, ticks and stamps all reach
    the model. Model boxes are page-relative, so they stay valid."""
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover
        raise LLMParseError("PyMuPDF is not installed; PDF pages cannot be rendered") from exc
    out: list[tuple[bytes, str]] = []
    try:
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            for n in range(first - 1, last):
                page = doc[n]
                long_pt = max(page.rect.width, page.rect.height) or 1.0
                zoom = min(PAGE_LONG_SIDE_PX / long_pt, 300 / 72)
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                data = pix.tobytes("jpeg", jpg_quality=90)
                if len(data) > IMAGE_MAX_BYTES:
                    data = pix.tobytes("jpeg", jpg_quality=75)
                out.append((data, "image/jpeg"))
    except Exception as exc:  # noqa: BLE001
        raise LLMParseError(f"the PDF pages could not be rendered: {type(exc).__name__}: {exc}") from exc
    return out


def prepare_image(file_bytes: bytes, filename: str | None) -> tuple[bytes, str]:
    """``(bytes, media_type)`` the model accepts: PNG/JPEG/GIF/WebP pass when
    small enough; TIFF/BMP (not accepted by the API) and oversize images are
    re-encoded as PNG at most ``IMAGE_LONG_SIDE_PX`` on the long side."""
    ext = _ext(filename)
    head = bytes(file_bytes or b"")[:8]
    mime = _IMAGE_MIME.get(ext)
    if mime is None:
        if head.startswith(b"\x89PNG"):
            mime = "image/png"
        elif head.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
    if mime and len(file_bytes) <= IMAGE_MAX_BYTES and ext not in _CONVERT_EXT:
        return file_bytes, mime
    try:
        import fitz
        pix = fitz.Pixmap(file_bytes)
        if pix.alpha or pix.n > 3:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        long_side = max(pix.width, pix.height)
        if long_side > IMAGE_LONG_SIDE_PX:
            shrink = 0
            while (long_side >> shrink) > IMAGE_LONG_SIDE_PX:
                shrink += 1
            pix.shrink(shrink)
        png = pix.tobytes("png")
        if len(png) > IMAGE_MAX_BYTES:
            return pix.tobytes("jpeg", jpg_quality=85), "image/jpeg"
        return png, "image/png"
    except Exception as exc:  # noqa: BLE001
        raise LLMParseError(f"the image could not be prepared for the model: {type(exc).__name__}: {exc}") from exc


# --------------------------------------------------------------------------
# The request
# --------------------------------------------------------------------------

_CLIENT: Any = None


def _bedrock_client() -> Any:
    """``bedrock-runtime`` with a read timeout long enough for a full answer
    (the boto default of 60 s cuts off a 30k-token transcription).

    Auth: the Bedrock API key when one is set (``AWS_BEARER_TOKEN_BEDROCK``,
    user decision 2026-10-01 — no IAM identity for Bedrock); botocore ≥ 1.39
    reads that variable itself and sends ``Authorization: Bearer``. Without
    it, the boto3 credential chain signs."""
    global _CLIENT
    if _CLIENT is None:
        import boto3
        from botocore.config import Config
        try:
            from ..cost_governance import bedrock_api_key, bedrock_region
        except ImportError:  # pragma: no cover
            from cost_governance import bedrock_api_key, bedrock_region  # type: ignore
        bedrock_api_key()  # copies the ASSURE_BEDROCK_API_KEY alias onto the botocore name
        region = bedrock_region()
        _CLIENT = boto3.client("bedrock-runtime", region_name=region,
                               config=Config(read_timeout=TIMEOUT_S, connect_timeout=10,
                                             retries={"max_attempts": 3, "mode": "adaptive"}))
    return _CLIENT


def _image_block(img: bytes, mime: str) -> dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(img).decode("ascii")}}


def _content_blocks(file_bytes: bytes, filename: str | None, first: int, last: int) -> list[dict[str, Any]]:
    """The page pictures of one request: rendered PDF pages ``first..last``,
    each preceded by its page number, or the uploaded image itself."""
    if _is_pdf(filename, file_bytes):
        blocks: list[dict[str, Any]] = []
        for n, (img, mime) in enumerate(pdf_page_images(file_bytes, first, last), start=1):
            blocks.append({"type": "text", "text": f"Page {n}:"})
            blocks.append(_image_block(img, mime))
        return blocks
    img, mime = prepare_image(file_bytes, filename)
    return [_image_block(img, mime)]


def _invoke(content: list[dict[str, Any]], *, model: str, page_hint: str = "") -> tuple[str, dict[str, Any]]:
    """One ``invoke_model`` call → ``(answer_text, meta)``; raises LLMParseError."""
    t0 = time.monotonic()
    prompt = PROMPT + (f"\n{page_hint}\n" if page_hint else "")
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": MAX_OUTPUT_TOKENS,
        "messages": [{"role": "user", "content": content + [{"type": "text", "text": prompt}]}],
    }
    ledger_model = f"bedrock/{_bare(model)}"
    try:
        resp = _bedrock_client().invoke_model(modelId=_bare(model), body=json.dumps(body),
                                              contentType="application/json", accept="application/json")
        out = json.loads(resp["body"].read())
    except Exception as exc:  # noqa: BLE001
        status = getattr(getattr(exc, "response", None), "get", lambda *_: None)("ResponseMetadata") or {}
        _mc.record(model=ledger_model, status="error", ms=(time.monotonic() - t0) * 1000,
                   http_status=status.get("HTTPStatusCode") if isinstance(status, dict) else None,
                   error=f"{type(exc).__name__}: {str(exc)[:300]}", stage="parse", path="bedrock_invoke")
        raise LLMParseError(f"Bedrock {_bare(model)} could not read the document: {type(exc).__name__}: {str(exc)[:300]}") from exc
    text = "".join(str(b.get("text") or "") for b in (out.get("content") or []) if isinstance(b, dict) and b.get("type") == "text")
    usage = out.get("usage") or {}
    meta = {"model": model, "ms": int((time.monotonic() - t0) * 1000), "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"), "stop_reason": out.get("stop_reason")}
    _mc.record(model=ledger_model, status="ok", ms=meta["ms"], http_status=200, prompt_chars=len(prompt), completion_chars=len(text),
               input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"), stage="parse", path="bedrock_invoke")
    return text, meta


def _pages_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    pages = data.get("pages")
    if isinstance(pages, list) and pages:
        return [p for p in pages if isinstance(p, dict)]
    # A single-page answer without the pages wrapper is accepted as page 1.
    return [data] if any(k in data for k in ("lines", "tables", "key_values")) else []


def _read_range(file_bytes: bytes, filename: str, first: int, last: int, total: int, *, model: str,
                completion: Any = None, depth: int = 0) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pages ``first..last`` of a PDF (or the one image when ``total`` is 0)
    → ``(answer_pages renumbered to the file's page numbers, request metas)``.

    An answer cut off at the output ceiling is re-read in two halves (up to a
    single page); a single page that still does not fit is kept with its
    ``truncated`` flag rather than dropped."""
    count = 1 if total == 0 else last - first + 1
    hint = (f"The {count} picture(s) above are the page(s) of one document, in order; return exactly {count} "
            f"entries in \"pages\", one per picture." if total else "")
    if completion is not None:
        whole = total == 0 or (first == 1 and last == total)
        raw = completion(PROMPT, file_bytes if whole else pdf_slice(file_bytes, first, last), filename)
        meta = {"model": model, "ms": 0, "stop_reason": "end_turn"}
    else:
        raw, meta = _invoke(_content_blocks(file_bytes, filename, first, last), model=model, page_hint=hint)
    meta = dict(meta, first_page=first, last_page=last)
    data = parse_answer(raw)
    truncated = meta.get("stop_reason") == "max_tokens" or data is None and bool(raw.strip())
    if truncated and count > 1 and depth < 4:
        mid = first + count // 2 - 1
        a_pages, a_meta = _read_range(file_bytes, filename, first, mid, total, model=model, completion=completion, depth=depth + 1)
        b_pages, b_meta = _read_range(file_bytes, filename, mid + 1, last, total, model=model, completion=completion, depth=depth + 1)
        return a_pages + b_pages, [dict(meta, superseded=True)] + a_meta + b_meta
    if data is None:
        raise LLMParseError(f"the model answered pages {first}–{last} without a JSON object (stop_reason={meta.get('stop_reason')})")
    entries = _pages_of(data)
    out: list[dict[str, Any]] = []
    for idx, entry in enumerate(entries[:count]):
        stated = entry.get("page")
        entry = dict(entry)
        entry["_page"] = first + idx
        entry["_stated_page"] = stated
        if truncated:
            entry["_truncated"] = True
        out.append(entry)
    return out, [meta]


def read_document(file_bytes: bytes, filename: str, *, model: str, completion: Any = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The whole file → ``(answer pages numbered 1..N, call meta)``.

    A PDF is read in ranges of ``PAGES_PER_REQUEST`` pages, in parallel; an
    image is one request. ``completion(prompt, bytes, filename) -> str`` may be
    injected (tests). Raises ``LLMParseError`` when nothing usable came back."""
    t0 = time.monotonic()
    if not file_bytes:
        raise LLMParseError("the file is empty")
    if _is_pdf(filename, file_bytes):
        total = pdf_page_count(file_bytes)
        if total <= 0:
            raise LLMParseError("the PDF could not be opened (damaged or encrypted)")
        pages_read = min(total, MAX_PAGES)
        ranges = [(s, min(s + PAGES_PER_REQUEST - 1, pages_read)) for s in range(1, pages_read + 1, PAGES_PER_REQUEST)]
    else:
        total, pages_read, ranges = 0, 1, [(1, 1)]
    results: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]
    if len(ranges) == 1:
        results = [_read_range(file_bytes, filename, ranges[0][0], ranges[0][1], total, model=model, completion=completion)]
    else:
        # Each worker thread inherits the ledger's stage context (contextvars).
        import contextvars
        with ThreadPoolExecutor(max_workers=max(1, min(CONCURRENCY, len(ranges)))) as pool:
            futures = [pool.submit(contextvars.copy_context().run, _read_range, file_bytes, filename, a, b, total,
                                   model=model, completion=completion) for a, b in ranges]
            results = [f.result() for f in futures]
    pages: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    for got, metas in results:
        pages.extend(got)
        requests.extend(metas)
    if not pages:
        raise LLMParseError("the model's answer held no pages")
    pages.sort(key=lambda p: p["_page"])
    # Renumber densely: a range that returned fewer entries than it had pages
    # must not leave a gap that shifts rasters against text.
    for i, p in enumerate(pages, start=1):
        p["_page"] = i
    meta = {
        "model": model,
        "ms": int((time.monotonic() - t0) * 1000),
        "requests": requests,
        "input_tokens": sum(int(r.get("input_tokens") or 0) for r in requests if not r.get("superseded")),
        "output_tokens": sum(int(r.get("output_tokens") or 0) for r in requests if not r.get("superseded")),
        "file_pages": total or 1,
        "pages_read": pages_read,
        "pages_returned": len(pages),
        "truncated_pages": [p["_page"] for p in pages if p.get("_truncated")],
        "page_limit_hit": bool(total and total > MAX_PAGES),
    }
    return pages, meta


def _rect(b: Any) -> list[float] | None:
    """A model box as ``[x0, y0, x1, y1]`` page fractions (scaled like ``_box``)."""
    g = _box(b)
    if not g:
        return None
    bb = g["BoundingBox"]
    return [round(bb["Left"], 4), round(bb["Top"], 4), round(bb["Left"] + bb["Width"], 4), round(bb["Top"] + bb["Height"], 4)]


def _text(v: Any) -> str:
    return "" if v is None else str(v)


def analysis_of(answer_pages: list[dict[str, Any]], *, model: str) -> dict[str, Any]:
    """Opus's own reading, kept as it answered (user decision 2026-10-01: the
    upload goes to Opus, its analysis is saved, and the site draws the page
    from that analysis — not from a derived JDF). Only the shape is cleaned:
    pages numbered 1..N, boxes as ``[x0, y0, x1, y1]`` fractions, strings."""
    pages: list[dict[str, Any]] = []
    for a in answer_pages:
        lines = [{"text": _text(l.get("text")).strip(), "bbox": _rect(l.get("bbox")),
                  "handwritten": l.get("handwritten") is True, "illegible": l.get("illegible") is True}
                 for l in (a.get("lines") or []) if isinstance(l, dict) and _text(l.get("text")).strip()]
        tables = []
        for t in a.get("tables") or []:
            if not isinstance(t, dict):
                continue
            rows = [[_text(c) for c in r] for r in (t.get("rows") or []) if isinstance(r, list)]
            headers = [_text(c) for c in (t.get("headers") or [])] if isinstance(t.get("headers"), list) else []
            if rows or headers:
                tables.append({"headers": headers, "rows": rows, "bbox": _rect(t.get("bbox"))})
        fields = [{"key": _text(kv.get("key")).strip(), "value": _text(kv.get("value")).strip(),
                   "bbox": _rect(kv.get("bbox")), "key_bbox": _rect(kv.get("key_bbox")),
                   "handwritten": kv.get("handwritten") is True, "illegible": kv.get("illegible") is True,
                   "kind": _text(kv.get("kind") or "text")}
                  for kv in (a.get("key_values") or []) if isinstance(kv, dict) and _text(kv.get("key")).strip()]
        pages.append({"page": a["_page"], "lines": lines, "tables": tables, "fields": fields,
                      "truncated": bool(a.get("_truncated"))})
    return {"model": _bare(model), "prompt_version": PROMPT_VERSION, "bbox_source": "model_estimate",
            "read_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "pages": pages}


def llm_parse_bundle(file_bytes: bytes, filename: str, *, completion: Any = None, model: str | None = None) -> dict[str, Any]:
    """The parse bundle for ``PARSER_BACKEND=bedrock`` — the keys
    ``jdf_converter.pdf_to_parse_bundle`` returns, the JDF in the scan shape."""
    try:
        from .jdf_converter import _bundle_assets, chunks_to_text
    except ImportError:  # pragma: no cover
        from services.jdf_converter import _bundle_assets, chunks_to_text  # type: ignore
    model = model or parse_model()
    answer_pages, meta = read_document(file_bytes, filename, model=model, completion=completion)
    pages = [{"page": p["_page"], "blocks": answer_to_blocks(p), "ms": 0} for p in answer_pages]
    sizes = _tj.page_sizes_mm(file_bytes, filename)
    bare = _bare(model)
    jdf, chunks, forms = _tj.build_jdf(pages, filename=filename, sizes_mm=sizes[: len(pages)], api=f"model:{bare}")
    jdf["meta"]["source"] = f"llm:{bare}"
    for f in forms:
        f["reader"] = "model"  # raw_candidates labels these "model read", not Textract
    for p in jdf["pages"]:
        for el in p["elements"]:
            if el.get("type") == "image" and isinstance(el.get("ocr"), dict):
                el["ocr"]["source"] = f"llm:{bare}"
                el["ocr"]["bbox_source"] = "model_estimate"
    assets = _bundle_assets(jdf, chunks)
    blocks_all = [b for p in pages for b in p["blocks"]]
    lines = sum(1 for b in blocks_all if b["BlockType"] == "LINE")
    handwritten = sum(1 for b in blocks_all if b["BlockType"] == "LINE" and b.get("TextType") == "HANDWRITING")
    illegible = sum(1 for b in blocks_all if b.get("Illegible"))
    return {
        "jdf": jdf,
        "chunks": chunks,
        "text": chunks_to_text(chunks),
        "page_count": len(jdf["pages"]),
        "parser_name": f"llm:{bare}",
        "source_kind": "scanned",
        "parse_confidence": None,
        "ocr_confidence": None,  # a model reports no measured read confidence
        "ocr_line_count": lines,
        "ocr_engine": f"llm:{bare}",
        "orientation": None,
        "wrapped_image": False,
        "forms": forms,
        "analysis": analysis_of(answer_pages, model=model),
        "llm_parse": {
            "model": model,
            "request": meta,
            "pages": [{"page": p["page"], "blocks": len(p["blocks"]), "stated_page": a.get("_stated_page"),
                       "truncated": bool(a.get("_truncated"))} for p, a in zip(pages, answer_pages)],
            "handwritten_lines": handwritten,
            "illegible_marks": illegible,
            "prompt_version": PROMPT_VERSION,
        },
        "filename": filename,
        **assets,
    }
