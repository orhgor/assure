"""Amazon Textract as the parser, its reading written as a JDF document.

Why (user decision 2026-09-29): every PDF and image goes to Textract — scans
and digital PDFs alike — and the pipeline continues on JDF exactly as it does
after jdf-cli, so the workbench shows the page as a JDF source (jdf.js, bbox
highlights, select-to-ask) whichever engine read it. Until now the Textract
path produced a stub JDF (``pages: [{}]``, one chunk per paragraph, no
geometry), so nothing downstream — layout, table pass, source view, page
rasters — had anything to work with.

Shape: the one jdf-cli emits for a scanned page, which every reader already
understands (``field_extractor.page_layout``, ``quality_probe``,
``source_jdf``, ``table_extraction.collect_tables``, jdf.js):

* one ``image`` element per page (``id: scan-<n>``, full-page, ``fit: fill``)
  whose ``ocr.blocks`` are Textract's LINE blocks — ``text``, ``confidence``
  (0–1) and a normalised ``bbox {x, y, w, h}``;
* one ``table`` element per Textract TABLE block (``headers`` = first row,
  ``rows`` = the rest), positioned from its bounding box;
* KEY_VALUE_SET pairs on ``bundle["forms"]`` with their page and bboxes.

Page size is in mm (jdf-cli's unit): a PDF page from its MediaBox, an image
from its pixel grid at 96 dpi (1080 px → 285.75 mm, what jdf-cli wrote for
the same photo). Chunks are built here (one per page image, one per table)
in jdf-cli's chunk shape, so ``jdf chunk`` is not needed for this path.

Calls: one Textract request per page — ``AnalyzeDocument`` (TABLES + FORMS)
when ``ASSURE_TEXTRACT_MODE=analyze``, else ``DetectDocumentText`` — each
charged against the monthly cap (``services/textract_budget``) before it is
sent. Confidence figures are Textract's own; ``parse_confidence`` stays
``None`` (there is no text layer to be confident about).
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

try:
    from ..lib.textract import TextractClient, TextractError
except ImportError:  # pragma: no cover - flat-import fallback
    from lib.textract import TextractClient, TextractError  # type: ignore

#: jdf-cli's page unit and the dpi it assumes for a raster without metadata.
MM_PER_PT = 25.4 / 72.0
MM_PER_PX_96DPI = 25.4 / 96.0
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _is_image(filename: str | None, data: bytes) -> bool:
    name = str(filename or "").lower()
    if name.endswith(IMAGE_EXTENSIONS):
        return True
    head = bytes(data or b"")[:8]
    return head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*", b"BM"))


def page_sizes_mm(file_bytes: bytes, filename: str | None) -> list[tuple[float, float]]:
    """``(width_mm, height_mm)`` per page: MediaBox for a PDF, pixels at 96 dpi
    for an image. Empty when the bytes do not open (the caller then uses A4)."""
    try:
        import fitz  # PyMuPDF
    except ImportError:  # pragma: no cover
        return []
    try:
        if _is_image(filename, file_bytes):
            doc = fitz.open(stream=file_bytes, filetype=str(filename or "img").rsplit(".", 1)[-1] if filename and "." in filename else None)
            try:
                pix_w, pix_h = doc[0].rect.width, doc[0].rect.height
            finally:
                doc.close()
            # fitz reports an image page in points at the file's dpi (72 when
            # none); pixels are what we want at 96 dpi like jdf-cli.
            return [(round(pix_w * MM_PER_PT * 72 / 96, 2), round(pix_h * MM_PER_PT * 72 / 96, 2))]
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            return [(round(p.rect.width * MM_PER_PT, 2), round(p.rect.height * MM_PER_PT, 2)) for p in doc]
    except Exception as exc:  # noqa: BLE001 — sizes are layout metadata, not the parse
        log.warning("textract_jdf: page sizes unavailable for %s: %s", filename, exc)
        return []


def _bbox(block: dict[str, Any]) -> dict[str, float] | None:
    box = ((block.get("Geometry") or {}).get("BoundingBox")) or {}
    try:
        return {"x": round(float(box["Left"]), 4), "y": round(float(box["Top"]), 4),
                "w": round(float(box["Width"]), 4), "h": round(float(box["Height"]), 4)}
    except (KeyError, TypeError, ValueError):
        return None


def _confidence(block: dict[str, Any]) -> float | None:
    try:
        return round(float(block.get("Confidence")) / 100.0, 4)
    except (TypeError, ValueError):
        return None


def ocr_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Textract LINE blocks in jdf-cli's ``ocr.blocks`` shape, reading order kept."""
    out: list[dict[str, Any]] = []
    for b in blocks:
        if b.get("BlockType") != "LINE" or not str(b.get("Text") or "").strip():
            continue
        entry: dict[str, Any] = {"text": str(b["Text"]).strip()}
        conf = _confidence(b)
        if conf is not None:
            entry["confidence"] = conf
        bbox = _bbox(b)
        if bbox is not None:
            entry["bbox"] = bbox
        out.append(entry)
    return out


def table_elements(blocks: list[dict[str, Any]], page_no: int, size_mm: tuple[float, float]) -> list[dict[str, Any]]:
    """Textract TABLE blocks as jdf-cli ``table`` elements (headers = first row)."""
    client = TextractClient(client=object())  # grid helpers only; no boto3 call
    bmap = client._block_map(blocks)  # noqa: SLF001
    width_mm, height_mm = size_mm
    out: list[dict[str, Any]] = []
    k = 0
    for b in blocks:
        if b.get("BlockType") != "TABLE":
            continue
        grid = client._table_to_grid(b, bmap)  # noqa: SLF001
        if not grid:
            continue
        k += 1
        headers = [str(c) for c in grid[0]] if len(grid) > 1 else []
        rows = [[str(c) for c in row] for row in (grid[1:] if len(grid) > 1 else grid)]
        el: dict[str, Any] = {"type": "table", "id": f"tbl-{page_no}-{k}", "headers": headers, "rows": rows, "style": {"fontSize": 8}}
        bbox = _bbox(b)
        if bbox:
            el["position"] = {"x": round(bbox["x"] * width_mm, 2), "y": round(bbox["y"] * height_mm, 2)}
            el["width"] = round(bbox["w"] * width_mm, 2)
            el["height"] = round(bbox["h"] * height_mm, 2)
            el["bbox"] = bbox
        conf = _confidence(b)
        if conf is not None:
            el["confidence"] = conf
        out.append(el)
    return out


def form_pairs(blocks: list[dict[str, Any]], page_no: int) -> list[dict[str, Any]]:
    """KEY_VALUE_SET pairs with page and bboxes (``bundle["forms"]``)."""
    client = TextractClient(client=object())
    bmap = client._block_map(blocks)  # noqa: SLF001
    out: list[dict[str, Any]] = []
    for key_block in blocks:
        if key_block.get("BlockType") != "KEY_VALUE_SET" or "KEY" not in (key_block.get("EntityTypes") or []):
            continue
        key_text = client._kv_text(key_block, bmap)  # noqa: SLF001
        value_text, value_bbox = "", None
        for rel in key_block.get("Relationships") or []:
            if rel.get("Type") != "VALUE":
                continue
            for vid in rel.get("Ids") or []:
                val = bmap.get(vid)
                if val:
                    value_text = client._kv_text(val, bmap)  # noqa: SLF001
                    value_bbox = _bbox(val)
        if key_text:
            out.append({"key": key_text, "value": value_text, "page": page_no, "key_bbox": _bbox(key_block), "value_bbox": value_bbox,
                        "confidence": _confidence(key_block)})
    return out


def _chunk(cid: str, text: str, page_no: int, types: list[str]) -> dict[str, Any]:
    return {"id": cid, "text": text, "path": [], "page": page_no, "types": types,
            "tokens": max(1, len(text.split())), "hash": hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]}


def build_jdf(pages: list[dict[str, Any]], *, filename: str | None, sizes_mm: list[tuple[float, float]], api: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """``(jdf, chunks, forms)`` from per-page Textract block lists.

    ``pages`` items: ``{"page": n, "blocks": [...]}``. A page without a size
    gets A4 (210 × 297) — layout metadata, stated in ``meta.page_size_source``.
    """
    jdf_pages: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    forms: list[dict[str, Any]] = []
    default_size = (210.0, 297.0)
    for entry in pages:
        n = int(entry.get("page") or (len(jdf_pages) + 1))
        blocks = list(entry.get("blocks") or [])
        size = sizes_mm[n - 1] if 0 <= n - 1 < len(sizes_mm) else default_size
        width_mm, height_mm = size
        lines = ocr_blocks(blocks)
        text = "\n".join(l["text"] for l in lines)
        image_el: dict[str, Any] = {
            "type": "image", "resource": f"img{n - 1}", "position": {"x": 0, "y": 0}, "width": width_mm, "height": height_mm,
            "fit": "fill", "id": f"scan-{n}",
            "ocr": {"language": "eng", "source": f"textract:{api}", "created": _now(), "blocks": lines},
        }
        elements: list[dict[str, Any]] = [image_el] + table_elements(blocks, n, size)
        jdf_pages.append({"id": f"page-{n}", "pageSize": {"width": width_mm, "height": height_mm},
                          "margins": {"top": 0, "right": 0, "bottom": 0, "left": 0}, "elements": elements})
        if text.strip():
            chunks.append(_chunk(f"scan-{n}", text, n, ["image"]))
        for el in elements[1:]:
            rows = ([el["headers"]] if el.get("headers") else []) + list(el.get("rows") or [])
            caption = "\n".join(" | ".join(str(c) for c in row) for row in rows)
            if caption.strip():
                chunks.append(_chunk(str(el["id"]), caption, n, ["table"]))
        forms.extend(form_pairs(blocks, n))
    first = sizes_mm[0] if sizes_mm else default_size
    jdf = {
        "$jdf": "1.0",
        "meta": {"title": str(filename or "document"), "pageSize": {"width": first[0], "height": first[1]}, "unit": "mm",
                 "margins": {"top": 0, "right": 0, "bottom": 0, "left": 0}, "source": f"textract:{api}",
                 "page_size_source": "document" if sizes_mm else "default_a4"},
        "pages": jdf_pages,
        "resources": {},
    }
    return jdf, chunks, forms


class TextractPages:
    """Per-page Textract calls, one request per page, budget-charged first."""

    def __init__(self, client: TextractClient | None = None) -> None:
        self.client = client or TextractClient()

    def analyze(self, file_bytes: bytes, filename: str) -> tuple[list[dict[str, Any]], str]:
        """``([{"page", "blocks", "ms"}], api)``; raises ``TextractError``.

        A client that offers only ``extract_text`` (an injected stand-in) is
        read through that: its page texts become LINE blocks without geometry,
        so the JDF still carries the text and the ledger says ``text_only``."""
        if not isinstance(self.client, TextractClient):
            extracted = self.client.extract_text(file_bytes, filename)
            pages = extracted.get("pages") or [{"page": 1, "text": extracted.get("text") or ""}]
            out = []
            for p in pages:
                lines = [ln for ln in str(p.get("text") or "").splitlines() if ln.strip()]
                out.append({"page": int(p.get("page") or len(out) + 1), "ms": 0,
                            "blocks": [{"BlockType": "LINE", "Text": ln} for ln in lines]})
            return out, "text_only"
        api = self.client.mode()
        if _is_image(filename, file_bytes) or not str(filename or "").lower().endswith(".pdf"):
            blobs = [file_bytes]
        else:
            blobs = self.client._split_pdf_pages(file_bytes)  # noqa: SLF001
        max_pages = self.client.TEXTRACT_MAX_PAGES
        blobs = blobs[:max_pages]
        self.client._charge(len(blobs), api)  # noqa: SLF001
        boto = self.client._boto_client()  # noqa: SLF001
        out: list[dict[str, Any]] = []
        for idx, blob in enumerate(blobs, start=1):
            t0 = time.monotonic()
            try:
                if api == "analyze":
                    resp = self.client._analyze_document_with_retry(blob)  # noqa: SLF001
                else:
                    resp = boto.detect_document_text(Document={"Bytes": blob})
            except TextractError:
                raise
            except Exception as exc:  # noqa: BLE001 — one clean error type for callers
                raise TextractError(f"Textract {api} failed on page {idx}: {exc}") from exc
            out.append({"page": idx, "blocks": list(resp.get("Blocks") or []), "ms": int((time.monotonic() - t0) * 1000)})
        return out, api


def textract_parse_bundle(file_bytes: bytes, filename: str, *, client: TextractClient | None = None, source_kind: str = "scanned") -> dict[str, Any]:
    """The parse bundle for the router's ``textract`` decision — the same keys
    ``jdf_converter.pdf_to_parse_bundle`` returns, so every caller (import
    route, Sources upload, legacy /jdf/ingest) continues on JDF unchanged."""
    try:
        from .jdf_converter import _bundle_assets, chunks_to_text
        from .parser_router import parser_backend
    except ImportError:  # pragma: no cover
        from services.jdf_converter import _bundle_assets, chunks_to_text  # type: ignore
        from services.parser_router import parser_backend  # type: ignore
    if parser_backend() == "openrouter" and client is None or (parser_backend() == "openrouter" and isinstance(client, TextractClient)):
        # The hosted-reader branch of the router covers the model too
        # (PARSER_BACKEND=openrouter, 2026-09-29): same bundle, read by the
        # parse-stage model instead of Textract.
        try:
            from .llm_parse import llm_parse_bundle
        except ImportError:  # pragma: no cover
            from services.llm_parse import llm_parse_bundle  # type: ignore
        return llm_parse_bundle(file_bytes, filename)
    pages, api = TextractPages(client).analyze(file_bytes, filename)
    sizes = page_sizes_mm(file_bytes, filename)
    jdf, chunks, forms = build_jdf(pages, filename=filename, sizes_mm=sizes, api=api)
    confidences = [b["confidence"] for p in jdf["pages"] for el in p["elements"] if el.get("type") == "image"
                   for b in (el.get("ocr") or {}).get("blocks") or [] if isinstance(b.get("confidence"), (int, float))]
    ocr_confidence = round(sum(confidences) / len(confidences), 4) if confidences else None
    assets = _bundle_assets(jdf, chunks)
    return {
        "jdf": jdf,
        "chunks": chunks,
        "text": chunks_to_text(chunks),
        "page_count": len(jdf["pages"]),
        "parser_name": "textract",
        "source_kind": source_kind,
        "parse_confidence": None,
        "ocr_confidence": ocr_confidence,
        "ocr_line_count": len(confidences),
        "ocr_engine": f"textract:{api}",
        "orientation": None,
        "wrapped_image": False,
        "forms": forms,
        "textract": {"api": api, "pages": [{"page": p["page"], "blocks": len(p["blocks"]), "ms": p["ms"]} for p in pages],
                     "region": getattr(client or TextractClient(), "_region", os.environ.get("AWS_REGION", ""))},
        "filename": filename,
        **assets,
    }
