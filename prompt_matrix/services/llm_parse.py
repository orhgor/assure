"""A hosted multimodal model as the parser (``PARSER_BACKEND=openrouter``).

User decision 2026-09-29: the documents are read by the analysis model itself
— OpenRouter, ``ASSURE_OPENROUTER_MODEL_PARSE`` (``anthropic/claude-opus-5.5``
at the time) — instead of Textract, and the reading is saved as a JDF document
so everything downstream (layout, table pass, raw candidates, source view,
page rasters) continues exactly as after Textract or jdf-cli.

How: every page is rendered to a PNG (``services/vision.render_page_png``, the
one PyMuPDF path the probe and the vision pass use) and sent, one request per
page, with a transcription prompt that asks for JSON only: the page's lines in
reading order with an approximate normalised box, its tables, and the labelled
key/value pairs it states. The answer is written into the same JDF shape
``services/textract_jdf.build_jdf`` produces for Textract — one full-page
``image`` element per page whose ``ocr.blocks`` are the lines, ``table``
elements, ``bundle.forms`` — by translating it into Textract-style blocks.

What is *not* claimed: a model gives no measured read confidence, so
``ocr_confidence`` is ``None`` (the page-quality score then rests on the
probe and coverage, never on a number the model made up), and the boxes are
the model's estimate (``bbox_source: model_estimate`` in the page's ``ocr``),
good enough to point the reader at the region, not a measurement. Each call is
booked to the ledger under the ``parse`` stage; the per-stage budget is one
request per page.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
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
    usable JSON on any page). Callers report it; nothing is fabricated."""


#: Longest side of the page raster the model sees (a letter page ≈ 190 dpi).
RENDER_LONG_SIDE_PX = int(os.environ.get("ASSURE_LLM_PARSE_LONG_SIDE_PX", "1568") or 1568)
MAX_OUTPUT_TOKENS = int(os.environ.get("ASSURE_LLM_PARSE_MAX_TOKENS", "8000") or 8000)
TIMEOUT_S = float(os.environ.get("ASSURE_LLM_PARSE_TIMEOUT_S", "120") or 120)
MAX_PAGES = int(os.environ.get("ASSURE_MAX_PAGES", "50") or 50)

PROMPT = (
    "You are transcribing one page of a business document (an insurance form, a claim, a report, a policy, an invoice).\n"
    "Return ONE JSON object and nothing else, with exactly these keys:\n"
    '  "lines": every line of text on the page in reading order, each {"text": "<verbatim>", "bbox": [x0, y0, x1, y1]} '
    "with the box as fractions of the page width/height (0–1, top-left origin), estimated;\n"
    '  "tables": each table as {"headers": [...], "rows": [[...], ...], "bbox": [x0, y0, x1, y1]};\n'
    '  "key_values": every labelled field the page states as {"key": "<label as printed>", "value": "<value as printed>", '
    '"bbox": [x0, y0, x1, y1]} — empty value when the field is blank.\n'
    "Copy text character for character. Do not translate, summarise, infer, normalise or compute anything. "
    "Do not add fields that are not printed. Ticked boxes read as [X], empty boxes as [ ]."
)


def parse_model() -> str:
    """The litellm id of the parse-stage model for the configured backend."""
    try:
        from .. import cost_governance as cg
    except ImportError:  # pragma: no cover
        import cost_governance as cg  # type: ignore
    backend = cg.llm_backend()
    if backend == "openrouter":
        return cg.openrouter_model("parse")
    if backend == "ollama":
        return cg.local_model("parse")
    policy = cg.CostGovernor().policy_for(cg.TaskType.FIELD_EXTRACTION)
    return policy.litellm_model or policy.model_id


def _api_kwargs(model: str) -> dict[str, Any]:
    try:
        from .. import cost_governance as cg
    except ImportError:  # pragma: no cover
        import cost_governance as cg  # type: ignore
    return dict(cg._litellm_api_kwargs(model))  # noqa: SLF001


def _render(file_bytes: bytes, filename: str, page_no: int) -> bytes:
    try:
        from .vision import render_page_png
    except ImportError:  # pragma: no cover
        from vision import render_page_png  # type: ignore
    return render_page_png(file_bytes, filename, page_no, long_side_px=RENDER_LONG_SIDE_PX)


def _page_count(file_bytes: bytes, filename: str) -> int:
    if _tj._is_image(filename, file_bytes):  # noqa: SLF001
        return 1
    try:
        import fitz

        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            return len(doc)
    except Exception as exc:  # noqa: BLE001
        raise LLMParseError(f"document does not open: {exc}") from exc


def _answer_text(resp: Any) -> str:
    try:
        from .model_utils import extract_litellm_response_text
    except ImportError:  # pragma: no cover
        from model_utils import extract_litellm_response_text  # type: ignore
    return extract_litellm_response_text(resp) or ""


def parse_answer(text: str) -> dict[str, Any] | None:
    """The JSON object in a model answer (code fences and prose around it
    tolerated; unbalanced brackets repaired), or None."""
    if not isinstance(text, str) or not text.strip():
        return None
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.M)
    start, end = body.find("{"), body.rfind("}")
    candidates = [body] + ([body[start:end + 1]] if 0 <= start < end else [])
    try:
        from .vision import _balance_brackets
    except ImportError:  # pragma: no cover
        _balance_brackets = None  # type: ignore
    for cand in list(candidates):
        if _balance_brackets is not None:
            candidates.append(_balance_brackets(cand))
    for cand in candidates:
        try:
            data = json.loads(cand)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict):
            return data
    return None


def _box(b: Any) -> dict[str, Any] | None:
    """A ``[x0, y0, x1, y1]`` fraction box → Textract-style BoundingBox."""
    try:
        x0, y0, x1, y1 = (float(v) for v in b)
    except (TypeError, ValueError):
        return None
    x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
    y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    return {"BoundingBox": {"Left": x0, "Top": y0, "Width": x1 - x0, "Height": y1 - y0}}


def answer_to_blocks(data: dict[str, Any]) -> list[dict[str, Any]]:
    """The model's answer as Textract-style blocks so ``textract_jdf.build_jdf``
    writes the same JDF for both readers: LINE (no Confidence — none was
    measured), TABLE/CELL/WORD, KEY_VALUE_SET/WORD."""
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
        b: dict[str, Any] = {"Id": _id("line"), "BlockType": "LINE", "Text": text}
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
            blocks.append({"Id": vw, "BlockType": "WORD", "Text": value})
            v_children = [vw]
        vb: dict[str, Any] = {"Id": vid, "BlockType": "KEY_VALUE_SET", "EntityTypes": ["VALUE"],
                              "Relationships": [{"Type": "CHILD", "Ids": v_children}] if v_children else []}
        geo = _box(kv.get("bbox"))
        if geo:
            vb["Geometry"] = geo
        blocks.append(vb)
        kb: dict[str, Any] = {"Id": _id("kv"), "BlockType": "KEY_VALUE_SET", "EntityTypes": ["KEY"],
                              "Relationships": [{"Type": "CHILD", "Ids": [kw]}, {"Type": "VALUE", "Ids": [vid]}]}
        if geo:
            kb["Geometry"] = geo
        blocks.append(kb)
    return blocks


def read_page(png: bytes, *, model: str, completion: Any = None) -> tuple[dict[str, Any], str, int]:
    """One model request for one page → ``(answer_dict, raw_text, ms)``.
    ``completion(prompt, png_bytes)`` may be injected (tests); the default is
    litellm with the image as a data URI, under the ``parse`` ledger stage."""
    t0 = time.monotonic()
    if completion is not None:
        raw = completion(PROMPT, png)
    else:
        import litellm  # type: ignore

        kwargs = _api_kwargs(model)
        data_uri = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        messages = [{"role": "user", "content": [{"type": "text", "text": PROMPT},
                                                 {"type": "image_url", "image_url": {"url": data_uri}}]}]
        with _mc.stage_context("parse"):
            resp = litellm.completion(model=model, messages=messages, max_tokens=MAX_OUTPUT_TOKENS, temperature=0.0,
                                      stream=False, timeout=int(TIMEOUT_S),
                                      metadata=_mc.litellm_metadata(kwargs.pop("metadata", None)), **kwargs)
        raw = _answer_text(resp)
    data = parse_answer(raw)
    if data is None:
        raise LLMParseError("the model answered without a JSON object")
    return data, raw, int((time.monotonic() - t0) * 1000)


def llm_parse_bundle(file_bytes: bytes, filename: str, *, completion: Any = None, model: str | None = None) -> dict[str, Any]:
    """The parse bundle for ``PARSER_BACKEND=openrouter`` — the keys
    ``jdf_converter.pdf_to_parse_bundle`` returns, the JDF in the scan shape."""
    try:
        from .jdf_converter import _bundle_assets, chunks_to_text
    except ImportError:  # pragma: no cover
        from services.jdf_converter import _bundle_assets, chunks_to_text  # type: ignore
    model = model or parse_model()
    count = min(_page_count(file_bytes, filename), MAX_PAGES)
    pages: list[dict[str, Any]] = []
    timings: list[dict[str, Any]] = []
    failures: list[str] = []
    for page_no in range(1, count + 1):
        try:
            png = _render(file_bytes, filename, page_no)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"page {page_no}: render failed: {exc}")
            continue
        try:
            data, _raw, ms = read_page(png, model=model, completion=completion)
        except Exception as exc:  # noqa: BLE001 — one page's failure is recorded, the others still read
            log.warning("llm_parse: %s page %s: %s", filename, page_no, exc)
            failures.append(f"page {page_no}: {type(exc).__name__}: {exc}"[:200])
            continue
        blocks = answer_to_blocks(data)
        pages.append({"page": page_no, "blocks": blocks, "ms": ms})
        timings.append({"page": page_no, "blocks": len(blocks), "ms": ms, "lines": sum(1 for b in blocks if b["BlockType"] == "LINE")})
    if not pages:
        raise LLMParseError("; ".join(failures) or "no pages")
    api = f"model:{model.split('/', 1)[-1] if model.startswith('openrouter/') else model}"
    sizes = _tj.page_sizes_mm(file_bytes, filename)
    jdf, chunks, forms = _tj.build_jdf(pages, filename=filename, sizes_mm=sizes, api=api)
    jdf["meta"]["source"] = f"llm:{model}"
    for p in jdf["pages"]:
        for el in p["elements"]:
            if el.get("type") == "image" and isinstance(el.get("ocr"), dict):
                el["ocr"]["source"] = f"llm:{model}"
                el["ocr"]["bbox_source"] = "model_estimate"
    assets = _bundle_assets(jdf, chunks)
    return {
        "jdf": jdf,
        "chunks": chunks,
        "text": chunks_to_text(chunks),
        "page_count": len(jdf["pages"]),
        "parser_name": f"llm:{model.split('/', 1)[-1] if model.startswith('openrouter/') else model}",
        "source_kind": "scanned",
        "parse_confidence": None,
        "ocr_confidence": None,  # a model reports no measured read confidence
        "ocr_line_count": sum(t["lines"] for t in timings),
        "ocr_engine": f"llm:{model}",
        "orientation": None,
        "wrapped_image": False,
        "forms": forms,
        "llm_parse": {"model": model, "pages": timings, "failed_pages": failures, "prompt_version": "llm-parse-v1"},
        "filename": filename,
        **assets,
    }
