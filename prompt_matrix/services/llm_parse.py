"""A hosted multimodal model as the parser (``PARSER_BACKEND=openrouter``).

User decision 2026-09-29: the documents are read by the analysis model itself
— OpenRouter, ``ASSURE_OPENROUTER_MODEL_PARSE`` (``anthropic/claude-opus-5.5``
at the time) — instead of Textract, and the reading is saved as a JDF document
so everything downstream (layout, table pass, raw candidates, source view,
page rasters) continues exactly as after Textract or jdf-cli.

How (user decision 2026-09-29: no PyMuPDF, no rendering — "the uploaded file
goes straight to Opus 5.5 over OpenRouter and every readable field and value is
written out"): the whole file is posted once to OpenRouter's chat completions —
a PDF as a ``file`` part read natively by the model, an image as a data URI —
with a transcription prompt that asks for JSON only: for every page, its lines
in reading order with an approximate normalised box, its tables, and the
labelled key/value pairs it states. The answer is written into the same JDF shape
``services/textract_jdf.build_jdf`` produces for Textract — one full-page
``image`` element per page whose ``ocr.blocks`` are the lines, ``table``
elements, ``bundle.forms`` — by translating it into Textract-style blocks.

What is *not* claimed: a model gives no measured read confidence, so
``ocr_confidence`` is ``None`` (the page-quality score then rests on the
probe and coverage, never on a number the model made up), and the boxes are
the model's estimate (``bbox_source: model_estimate`` in the page's ``ocr``),
good enough to point the reader at the region, not a measurement. The one
request is booked to the ledger under the ``parse`` stage with its token usage.
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


MAX_OUTPUT_TOKENS = int(os.environ.get("ASSURE_LLM_PARSE_MAX_TOKENS", "32000") or 32000)
TIMEOUT_S = float(os.environ.get("ASSURE_LLM_PARSE_TIMEOUT_S", "600") or 600)
MAX_PAGES = int(os.environ.get("ASSURE_MAX_PAGES", "50") or 50)

PROMPT = (
    "You are transcribing a business document (an insurance form, a claim, a report, a policy, an invoice) page by page.\n"
    "Return ONE JSON object and nothing else: {\"pages\": [ ... ]} with one entry per page of the document, in order, each\n"
    '  {"page": <1-based number>,\n'
    '   "lines": every line of text on that page in reading order, each {"text": "<verbatim>", "bbox": [x0, y0, x1, y1]} '
    "with the box as fractions of the page width/height (0–1, top-left origin), estimated;\n"
    '   "tables": each table on that page as {"headers": [...], "rows": [[...], ...], "bbox": [x0, y0, x1, y1]};\n'
    '   "key_values": every labelled field the page states as {"key": "<label as printed>", "value": "<value as printed>", '
    '"bbox": [x0, y0, x1, y1]} — empty value when the field is blank }.\n'
    "Read every page; do not stop early. Copy text character for character. Do not translate, summarise, infer, normalise or "
    "compute anything. Do not add fields that are not printed. Ticked boxes read as [X], empty boxes as [ ]."
)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".tif": "image/tiff",
         ".tiff": "image/tiff", ".bmp": "image/bmp", ".webp": "image/webp"}


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


def _mime(filename: str, data: bytes) -> str:
    ext = os.path.splitext(str(filename or "").lower())[1]
    if ext in _MIME:
        return _MIME[ext]
    head = bytes(data or b"")[:8]
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return "application/octet-stream"


def parse_answer(text: str) -> dict[str, Any] | None:
    """The JSON object in a model answer (code fences and prose around it
    tolerated; unbalanced brackets of a truncated answer repaired), or None."""
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
    """One page's answer as Textract-style blocks so ``textract_jdf.build_jdf``
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


def _content_part(file_bytes: bytes, filename: str) -> dict[str, Any]:
    """The file as OpenRouter wants it: a PDF as a ``file`` part (read natively
    by the model — ``plugins: file-parser, engine native``), an image as an
    ``image_url`` data URI."""
    mime = _mime(filename, file_bytes)
    b64 = base64.b64encode(file_bytes).decode("ascii")
    if mime == "application/pdf":
        return {"type": "file", "file": {"filename": os.path.basename(filename or "document.pdf"), "file_data": f"data:{mime};base64,{b64}"}}
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def read_document(file_bytes: bytes, filename: str, *, model: str, completion: Any = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """ONE request with the whole uploaded file (no rendering, no page
    splitting — user decision 2026-09-29) → ``(answer_dict, call_meta)``.

    The default path posts to OpenRouter's chat completions directly (the
    ``file`` content part and the ``file-parser`` plugin are OpenRouter's, not
    litellm's) and books the request in the model-call ledger under the
    ``parse`` stage. ``completion(prompt, file_bytes, filename)`` may be
    injected. Raises ``LLMParseError`` when nothing usable came back."""
    import urllib.error
    import urllib.request

    t0 = time.monotonic()
    meta: dict[str, Any] = {"model": model, "ms": 0, "input_tokens": None, "output_tokens": None, "http_status": None}
    if completion is not None:
        raw = completion(PROMPT, file_bytes, filename)
    else:
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            raise LLMParseError("OPENROUTER_API_KEY is empty: the model parser cannot send the document")
        bare = model.split("/", 1)[-1] if model.startswith("openrouter/") else model
        payload: dict[str, Any] = {
            "model": bare,
            "messages": [{"role": "user", "content": [{"type": "text", "text": PROMPT}, _content_part(file_bytes, filename)]}],
            "max_tokens": MAX_OUTPUT_TOKENS,
            "temperature": 0,
        }
        if _mime(filename, file_bytes) == "application/pdf":
            payload["plugins"] = [{"id": "file-parser", "pdf": {"engine": "native"}}]
        req = urllib.request.Request(
            OPENROUTER_URL, data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                     "HTTP-Referer": os.environ.get("OPENROUTER_REFERER", "https://getassureai.com"), "X-Title": "Assure parse"},
        )
        status: int | None = None
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 - fixed https URL
                status = resp.status
                body = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            _mc.record(model=f"openrouter/{bare}", status="error", ms=(time.monotonic() - t0) * 1000, http_status=exc.code,
                       error=f"HTTPError {exc.code}: {detail}", stage="parse", path="openrouter_rest")
            raise LLMParseError(f"OpenRouter answered {exc.code}: {detail}") from exc
        except Exception as exc:  # noqa: BLE001
            _mc.record(model=f"openrouter/{bare}", status="error", ms=(time.monotonic() - t0) * 1000,
                       error=f"{type(exc).__name__}: {exc}", stage="parse", path="openrouter_rest")
            raise LLMParseError(f"OpenRouter request failed: {type(exc).__name__}: {exc}") from exc
        if body.get("error"):
            _mc.record(model=f"openrouter/{bare}", status="error", ms=(time.monotonic() - t0) * 1000, http_status=status,
                       error=json.dumps(body["error"])[:300], stage="parse", path="openrouter_rest")
            raise LLMParseError(f"OpenRouter error: {json.dumps(body['error'])[:300]}")
        choice = (body.get("choices") or [{}])[0]
        raw = ((choice.get("message") or {}).get("content")) or ""
        if isinstance(raw, list):  # some providers return content parts
            raw = "".join(str(p.get("text") or "") for p in raw if isinstance(p, dict))
        usage = body.get("usage") or {}
        meta.update(input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"), http_status=status,
                    finish_reason=choice.get("finish_reason"))
        _mc.record(model=f"openrouter/{bare}", status="ok", ms=(time.monotonic() - t0) * 1000, http_status=status,
                   prompt_chars=len(PROMPT), completion_chars=len(raw), input_tokens=usage.get("prompt_tokens"),
                   output_tokens=usage.get("completion_tokens"), stage="parse", path="openrouter_rest")
    meta["ms"] = int((time.monotonic() - t0) * 1000)
    data = parse_answer(raw)
    if data is None:
        raise LLMParseError("the model answered without a JSON object")
    return data, meta


def llm_parse_bundle(file_bytes: bytes, filename: str, *, completion: Any = None, model: str | None = None) -> dict[str, Any]:
    """The parse bundle for ``PARSER_BACKEND=openrouter`` — the keys
    ``jdf_converter.pdf_to_parse_bundle`` returns, the JDF in the scan shape.
    The whole file goes to the model in one request; the model's own page list
    is the page count (no PDF library touches the file on this path)."""
    try:
        from .jdf_converter import _bundle_assets, chunks_to_text
    except ImportError:  # pragma: no cover
        from services.jdf_converter import _bundle_assets, chunks_to_text  # type: ignore
    model = model or parse_model()
    data, meta = read_document(file_bytes, filename, model=model, completion=completion)
    answer_pages = data.get("pages")
    if not isinstance(answer_pages, list) or not answer_pages:
        # A single-page answer without the pages wrapper is accepted as page 1.
        answer_pages = [data] if any(k in data for k in ("lines", "tables", "key_values")) else []
    pages: list[dict[str, Any]] = []
    for idx, entry in enumerate(answer_pages, start=1):
        if not isinstance(entry, dict):
            continue
        try:
            page_no = int(entry.get("page") or idx)
        except (TypeError, ValueError):
            page_no = idx
        blocks = answer_to_blocks(entry)
        pages.append({"page": page_no, "blocks": blocks, "ms": 0})
    if not pages:
        raise LLMParseError("the model's answer held no pages")
    pages.sort(key=lambda p: p["page"])
    api = f"model:{model.split('/', 1)[-1] if model.startswith('openrouter/') else model}"
    jdf, chunks, forms = _tj.build_jdf(pages, filename=filename, sizes_mm=[], api=api)
    jdf["meta"]["source"] = f"llm:{model}"
    for p in jdf["pages"]:
        for el in p["elements"]:
            if el.get("type") == "image" and isinstance(el.get("ocr"), dict):
                el["ocr"]["source"] = f"llm:{model}"
                el["ocr"]["bbox_source"] = "model_estimate"
    assets = _bundle_assets(jdf, chunks)
    lines = sum(1 for p in pages for b in p["blocks"] if b["BlockType"] == "LINE")
    return {
        "jdf": jdf,
        "chunks": chunks,
        "text": chunks_to_text(chunks),
        "page_count": len(jdf["pages"]),
        "parser_name": f"llm:{model.split('/', 1)[-1] if model.startswith('openrouter/') else model}",
        "source_kind": "scanned",
        "parse_confidence": None,
        "ocr_confidence": None,  # a model reports no measured read confidence
        "ocr_line_count": lines,
        "ocr_engine": f"llm:{model}",
        "orientation": None,
        "wrapped_image": False,
        "forms": forms,
        "llm_parse": {"model": model, "request": meta, "pages": [{"page": p["page"], "blocks": len(p["blocks"])} for p in pages],
                      "prompt_version": "llm-parse-v2-whole-document"},
        "filename": filename,
        **assets,
    }
