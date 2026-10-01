"""The source JDF: the raw jdf-cli document as a persisted, addressable artifact.

Until 2026-09-28 the document the models read — jdf-cli's ``pages[].elements[]``
with millimetre positions — lived only in memory (``bundle["jdf_source"]`` in
``services/pdf_ingest``, ``extracted["jdf"]`` in ``routers/substrate``) and was
gone once the revision was saved. The browser rendered the Assure tree, a
paragraph list without positions, so a reviewer could not see the page the
field came from nor point at a span of it. This module makes that document a
first-class artifact:

* ``persist_source_jdf`` writes it to the object store under
  ``documents/<project>/<document_id>/<revision or job id>.jdf`` (JSON,
  ``application/json``) so ``@uurtech/jdf``'s ``<jdf src="…/source.jdf">`` can
  render it, and records the key on the ingest job. Nothing is rendered here:
  page images are not part of the pipeline (``services/vision.render_page_png``
  renders one page for the multimodal read only, on demand, and stores
  nothing).
* ``element_index`` gives every text element the same ``eid-v1`` identity a
  Parsure field carries (``field_extractor.derive_element_id`` through
  ``page_layout``), so an id on a field, an id in the browser and an id in a
  selection anchor are the same string. The ids are stamped on the stored
  elements (``element["assure"]["element_id"]``) because jdf-cli 0.2.3 elements
  carry no id of their own and the chunk list the prefix comes from is not part
  of the stored document.
* ``find_elements_for_text`` maps a text selection made on the rendered page
  back to element ids and a union bbox — verbatim, whitespace-collapsed
  (``llm_extraction.find_verbatim``), the same comparison the claim policy uses
  for a quote. A selection that is not in the page text maps to nothing.
* ``SelectionAnchor`` / ``selection_anchor_meta``: the shape the compile and
  inquire streams accept and the ``meta.selection_anchor`` they write, with
  ``verbatim`` true only when the selection text was re-found in the cited
  sources' ``extracted_text`` — never assumed from the browser's word.

Page rasters (2026-09-28). A scanned or photographed page has only OCR text in
its source JDF, so the rendered page showed a reader the OCR's reading and not
the page — no signature, stamp or handwriting. For a document whose text came
from OCR (``pages_are_ocr``: parser ``jdf-cli+tesseract`` / ``textract``, or a
scan / photo / screenshot modality) the worker renders every page once
(``store_page_rasters``: the same PyMuPDF render ``services/vision`` uses, PNG,
long side ≤ ``RASTER_LONG_SIDE_PX``) into the object store under
``documents/<project>/<doc>/<revision>/pages/<n>.png`` and
``persist_source_jdf`` puts an ``image`` element first on each such page —
full-page, ``src`` = ``GET …/documents/<doc>/pages/<n>.png`` — so jdf.js draws
the page under the text. A digital PDF gets none: its text layer is the page.
Nothing is synthesised: a page that did not render has no element and the
descriptor's ``rasters`` count says how many did.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import struct
import time
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

try:
    from ..services import field_extractor as fx
    from ..services.llm_extraction import find_verbatim
    from ..services.object_store import _safe_segment, get_object_store
except ImportError:
    from services import field_extractor as fx  # type: ignore
    from services.llm_extraction import find_verbatim  # type: ignore
    from services.object_store import _safe_segment, get_object_store  # type: ignore

log = logging.getLogger(__name__)

#: Longest selection text the streams accept. A page of A4 body text is ~3,000
#: characters; a selection longer than that is a page, not an anchor.
SELECTION_TEXT_MAX_CHARS = 4000

#: Up to this many jobs are scanned for a project when resolving a document's
#: latest source JDF (the shell shows 50; 500 is the list route's own cap).
_JOB_SCAN_LIMIT = 500

#: Longest side of a stored page raster. 1600 px on a letter page is ~190 dpi:
#: a signature stroke and a stamp are legible, and a page is ~100–250 KB of PNG
#: (measured 2026-09-28 on the debris PDF: 100 KB at 1109×1568). The vision
#: render uses 1568 for the same reason; rasters are a separate render because
#: the vision pass only runs on picture pages.
RASTER_LONG_SIDE_PX = 1600
#: Pages rastered per document at most; a 200-page scan is the upload cap
#: (``limits.max_pages``) and 200 PNGs of ≤250 KB is 50 MB, which the store
#: holds but the worker should not spend on a single ingest without a bound.
RASTER_MAX_PAGES = 200
#: Substrings of a parser name that mean the text is an OCR reading
#: (``jdf_converter`` names the OCR path ``jdf-cli+<engine>``; Textract is OCR).
OCR_PARSER_MARKERS = ("tesseract", "textract", "openai", "llm:")
#: Modalities (``quality_probe.detect_material``) and source kinds whose page is
#: a picture, whatever the parser name says.
PICTURE_MODALITIES = ("scanned_pdf", "phone_photo", "screenshot")
PICTURE_SOURCE_KINDS = ("scanned", "photo", "image")
#: jdf.js named page sizes, in mm, for a page whose ``pageSize`` is a name.
_PAGE_SIZES_MM = {
    "A3": (297.0, 420.0), "A4": (210.0, 297.0), "A5": (148.0, 210.0),
    "Letter": (215.9, 279.4), "Legal": (215.9, 355.6), "Tabloid": (279.4, 431.8),
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_source_jdf(jdf: Any) -> bool:
    """Whether ``jdf`` is a jdf-cli document (``pages`` list, no Assure ``body``)."""
    return isinstance(jdf, dict) and isinstance(jdf.get("pages"), list) and not isinstance(jdf.get("body"), list)


def source_jdf_key(project_id: str, document_id: str, revision: str) -> str:
    """``documents/<project>/<document_id>/<revision>.jdf``.

    The project is the first segment so a key can be checked against the
    requesting project (``key_in_project``) the way ``object_store.
    key_belongs_to_project`` checks an upload key — a document id alone is a
    client-supplied string and cannot be trusted to name the caller's project.
    """
    return (
        f"documents/{_safe_segment(project_id, 'project')}/"
        f"{_safe_segment(document_id, 'document')}/{_safe_segment(revision, 'revision')}.jdf"
    )


def document_prefix(project_id: str, document_id: str) -> str:
    return f"documents/{_safe_segment(project_id, 'project')}/{_safe_segment(document_id, 'document')}/"


def key_in_project(key: str, project_id: str) -> bool:
    """Whether a stored key names this project — the guard before the route
    streams bytes the client asked for by document id."""
    return (
        bool(key)
        and key.startswith(f"documents/{_safe_segment(project_id, 'project')}/")
        and key.endswith(".jdf")
        and ".." not in key
    )


def source_jdf_url(project_id: str, document_id: str) -> str:
    return f"/api/projects/{project_id}/documents/{document_id}/source.jdf"


def page_raster_key(source_key: str, page_no: int) -> str:
    """``documents/<project>/<doc>/<revision>/pages/<n>.png`` beside the
    ``.jdf`` key of the same revision — derived from it, so the route that
    resolved a document's source key can name its rasters without a second
    lookup, and a raster can never belong to another revision's document."""
    if not source_key.endswith(".jdf"):
        raise ValueError(f"not a source JDF key: {source_key!r}")
    return f"{source_key[:-len('.jdf')]}/pages/{int(page_no)}.png"


def page_raster_url(project_id: str, document_id: str, page_no: int) -> str:
    return f"/api/projects/{project_id}/documents/{document_id}/pages/{int(page_no)}.png"


def original_key(source_key: str, filename: str | None) -> str:
    """``documents/<project>/<doc>/<revision>/original.<ext>`` beside the
    ``.jdf`` key of the same revision (derived like ``page_raster_key``)."""
    if not source_key.endswith(".jdf"):
        raise ValueError(f"not a source JDF key: {source_key!r}")
    ext = (str(filename or "").lower().rsplit(".", 1)[-1] if "." in str(filename or "") else "bin")
    ext = "".join(c for c in ext if c.isalnum())[:8] or "bin"
    return f"{source_key[:-len('.jdf')]}/original.{ext}"


def original_url(project_id: str, document_id: str) -> str:
    return f"/api/projects/{project_id}/documents/{document_id}/original"


def analysis_key(source_key: str) -> str:
    """``documents/<project>/<doc>/<revision>/analysis.json`` beside the ``.jdf``."""
    if not source_key.endswith(".jdf"):
        raise ValueError(f"not a source JDF key: {source_key!r}")
    return f"{source_key[:-len('.jdf')]}/analysis.json"


def analysis_url(project_id: str, document_id: str) -> str:
    return f"/api/projects/{project_id}/documents/{document_id}/analysis"


def store_analysis(project_id: str, document_id: str, revision: str, analysis: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep the model's own reading (``llm_parse.analysis_of``) beside the
    revision and return ``{"key", "url", "pages", "fields", "tables"}``, or None.

    User decision 2026-10-01: the upload goes to Opus, its analysis is saved,
    and the site draws the document from it — fields on the page, tables as
    tables. Never raises: a store failure is logged and the view falls back
    to the original alone."""
    if not isinstance(analysis, dict) or not isinstance(analysis.get("pages"), list):
        return None
    try:
        key = analysis_key(source_jdf_key(project_id, document_id, revision))
        payload = json.dumps(analysis, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        get_object_store().put_bytes(key, payload, content_type="application/json")
    except Exception:
        log.exception("analysis of %s/%s could not be stored", project_id, document_id)
        return None
    pages = [p for p in analysis["pages"] if isinstance(p, dict)]
    return {"key": key, "url": analysis_url(project_id, document_id), "pages": len(pages),
            "fields": sum(len(p.get("fields") or []) for p in pages),
            "tables": sum(len(p.get("tables") or []) for p in pages)}


def store_original(
    project_id: str,
    document_id: str,
    revision: str,
    file_bytes: bytes | None,
    filename: str | None,
    *,
    display: str,
) -> dict[str, Any] | None:
    """Keep the uploaded file beside its source JDF and return
    ``{"key", "url", "content_type", "display", "bytes"}``, or None.

    User decision 2026-10-01: the reviewer sees the document, not a re-drawn
    text dump — a digital PDF is shown as the PDF itself (``display: "pdf"``),
    a scan or photo as its page images with the fields placed on them
    (``display: "image"``). Until then the staged upload was deleted after
    intake (``uploads/`` also expires after a day), so ``…/original`` answered
    404 for nearly every document. Never raises: a store failure is logged and
    the UI falls back to the source JDF view."""
    if not file_bytes:
        return None
    try:
        import mimetypes
        key = original_key(source_jdf_key(project_id, document_id, revision), filename)
        content_type = mimetypes.guess_type(filename or "")[0] or "application/octet-stream"
        get_object_store().put_bytes(key, file_bytes, content_type=content_type)
    except Exception:
        log.exception("original of %s/%s could not be stored", project_id, document_id)
        return None
    return {
        "key": key,
        "url": original_url(project_id, document_id),
        "content_type": content_type,
        "display": display if display in ("pdf", "image") else "image",
        "bytes": len(file_bytes),
    }


def original_display(filename: str | None, *, modality: str | None, parser_name: str | None, source_kind: str | None) -> str:
    """``"pdf"`` for a PDF with a real text layer (shown as is), ``"image"``
    for a scan, a photo or any image upload (page rasters + placed fields).
    A model-read PDF is a ``"pdf"`` when the router measured it digital: the
    reader changes, the page does not."""
    name = str(filename or "").lower()
    if not name.endswith(".pdf"):
        return "image"
    mod = str(modality or "").lower()
    if mod in PICTURE_MODALITIES:
        return "image"
    if mod == "digital_pdf":
        return "pdf"
    return "image" if pages_are_ocr(parser_name, source_kind=source_kind) else "pdf"


def pages_are_ocr(parser_name: str | None, *, modality: str | None = None, source_kind: str | None = None) -> bool:
    """Whether the document's text is an OCR reading of a picture — the case in
    which the reader needs the page itself, not only its text. True for an OCR
    parser (``jdf-cli+tesseract``, ``textract``), a picture modality
    (``scanned_pdf``, ``phone_photo``, ``screenshot``) or a picture source kind
    (``scanned``, ``photo``, ``image``). A digital PDF (``jdf-cli``,
    ``digital_pdf``, ``pdf``) is False: its text layer is the page."""
    name = str(parser_name or "").lower()
    if any(marker in name for marker in OCR_PARSER_MARKERS):
        return True
    if str(modality or "").lower() in PICTURE_MODALITIES:
        return True
    return str(source_kind or "").lower() in PICTURE_SOURCE_KINDS


def content_revision(file_bytes: bytes | None) -> str:
    """The revision segment for a path with no revision and no job (the Sources
    panel's synchronous upload): the content hash, so the same bytes land on
    the same key and a re-upload overwrites rather than accumulates."""
    return "sha-" + hashlib.sha256(file_bytes or b"").hexdigest()[:16]


# --------------------------------------------------------------------------
# Element addressing
# --------------------------------------------------------------------------

def _text_elements(page: dict) -> list[dict]:
    """The elements of one page that ``page_layout`` turns into segments, in
    the order it walks them: nested elements included, empty text skipped."""
    return [el for el in fx._walk_elements(page.get("elements")) if fx._element_text(el).strip()]


def _indexed_elements(jdf: dict, chunks: list[dict] | None) -> list[tuple[int, dict, dict]]:
    """``(page_no, element, layout_segment)`` for every text element, the
    segment being ``page_layout``'s view of that element. One walk shared by
    the index and the stamping so the two can never disagree on order."""
    if not is_source_jdf(jdf):
        return []
    pages = [p for p in jdf["pages"] if isinstance(p, dict)]
    if not any(_text_elements(p) for p in pages):
        return []
    layouts = fx.page_layout({"jdf": jdf, "chunks": list(chunks or [])})
    out: list[tuple[int, dict, dict]] = []
    for page_no, (page, segs) in enumerate(zip(pages, layouts), start=1):
        for el, seg in zip(_text_elements(page), segs):
            out.append((page_no, el, seg))
    return out


def element_index(jdf: dict, chunks: list[dict] | None = None) -> dict[str, dict[str, Any]]:
    """``{element_id: {"page", "bbox_rel", "text", "chunk_id", "start", "end"}}``
    for every text element of a jdf-cli document.

    With ``chunks`` (the ``jdf chunk`` output of the same parse) the ids are
    derived exactly as ``field_extractor.page_layout`` derives them for the
    Parsure fields — same walk, same chunk attachment, same
    ``derive_element_id`` — so the two sets are equal by construction
    (``tests/test_source_jdf.py`` proves it on one bundle). Without chunks the
    stamped ``element["assure"]["element_id"]`` is used when the document was
    stored by ``persist_source_jdf``; a document with neither gets the
    chunk-less derivation (``p<page>:`` prefix), which is a different id space
    and is reported as such by callers that compare.
    """
    out: dict[str, dict[str, Any]] = {}
    for page_no, el, seg in _indexed_elements(jdf, chunks):
        stamped = (el.get("assure") or {}).get("element_id") if isinstance(el.get("assure"), dict) else None
        eid = seg.get("element_id") if chunks else (stamped or seg.get("element_id"))
        if not eid:
            continue
        out[str(eid)] = {
            "page": page_no,
            "bbox_rel": seg.get("bbox"),
            "text": seg.get("text") or "",
            "chunk_id": seg.get("chunk_id"),
            "start": seg.get("start"),
            "end": seg.get("end"),
            # OCR lines of a scanned page (jdf-cli tesseract, Textract): the
            # element is the whole page image, the lines carry the boxes a
            # text selection can be narrowed to (2026-09-29).
            "ocr_lines": [
                {"bbox": e.get("bbox"), "start_char": int(e.get("start_char") or 0), "end_char": int(e.get("end_char") or 0)}
                for e in (seg.get("elements") or []) if isinstance(e, dict) and e.get("kind") == "ocr_block" and e.get("bbox")
            ],
        }
    return out


def stamp_element_ids(jdf: dict, chunks: list[dict] | None) -> int:
    """Write ``element["assure"] = {"element_id", "policy"}`` on every text
    element, in place, from the chunk-aware derivation. Returns the number
    stamped."""
    count = 0
    for _page_no, el, seg in _indexed_elements(jdf, chunks):
        eid = seg.get("element_id")
        if not eid:
            continue
        assure = el.get("assure") if isinstance(el.get("assure"), dict) else {}
        assure.update({"element_id": str(eid), "policy": fx.NODE_ID_POLICY})
        el["assure"] = assure
        count += 1
    return count


def _union_bbox(boxes: list[list[float] | None]) -> list[float] | None:
    real = [b for b in boxes if isinstance(b, (list, tuple)) and len(b) == 4]
    if not real:
        return None
    return [
        round(min(b[0] for b in real), 4), round(min(b[1] for b in real), 4),
        round(max(b[2] for b in real), 4), round(max(b[3] for b in real), 4),
    ]


#: Characters the normalised comparison rewrites (2026-09-28). Ligatures are
#: what a PDF text layer often carries for "fi"/"fl"; curly quotes and the
#: non-breaking spaces are what a browser selection often carries instead of
#: the straight ASCII the page text has (or the reverse). Nothing here changes
#: a letter into a different letter: a repair is never a guess.
_NORMALISE_CHARS = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st",
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"',
    "\u00a0": " ", "\u202f": " ", "\u2007": " ", "\u2009": " ", "\u200a": " ", "\u3000": " ",
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
}
_SOFT_HYPHEN = "\u00ad"


def _normalise_with_map(text: str) -> tuple[str, list[int]]:
    """Lower-cased text with whitespace collapsed, soft hyphens removed, the
    line-wrap hyphen joined (``insur-\\nance`` / ``insur- ance`` → ``insurance``:
    a hyphen between a letter and a following whitespace + lower-case letter),
    ligatures expanded, curly quotes and dashes straightened and non-breaking
    spaces made plain — plus, for every kept character, the offset of the
    original character it came from, so a match maps back to an exact range
    in the page text (``_collapse_with_map`` in ``llm_extraction`` is the
    verbatim-only sibling)."""
    out: list[str] = []
    offsets: list[int] = []
    prev_space = True
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch == _SOFT_HYPHEN:
            i += 1
            continue
        if ch == "-" and out and out[-1].isalpha():
            # Line-wrap join: hyphen, whitespace, then a lower-case letter.
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            if j > i + 1 and j < n and text[j].isalpha() and text[j].islower():
                i = j
                continue
        rep = _NORMALISE_CHARS.get(ch)
        if rep is not None:
            ch = rep
        if ch.isspace() or ch == " ":
            if prev_space:
                i += 1
                continue
            out.append(" ")
            offsets.append(i)
            prev_space = True
            i += 1
            continue
        for c in ch:
            out.append(c.lower())
            offsets.append(i)
        prev_space = False
        i += 1
    if out and out[-1] == " ":
        out.pop()
        offsets.pop()
    return "".join(out), offsets


def normalise_text(text: str) -> str:
    return _normalise_with_map(str(text or ""))[0]


def find_normalised(page_text: str, needle: str) -> tuple[int, int] | None:
    """``(start, end)`` in ``page_text`` of ``needle`` under the normalised
    comparison, or None. Exact substring search on the normalised forms —
    no edit distance, no token overlap: a text that is not there is not found."""
    needle_n = normalise_text(needle)
    if not needle_n:
        return None
    hay, offsets = _normalise_with_map(page_text or "")
    pos = hay.find(needle_n)
    if pos < 0:
        return None
    return offsets[pos], offsets[pos + len(needle_n) - 1] + 1


def find_elements_for_text(
    jdf: dict, text: str, page: int | None = None, chunks: list[dict] | None = None
) -> dict[str, Any]:
    """The elements a text selection covers:
    ``{"element_ids", "page", "bbox_rel", "found", "matched_by"}``.

    The page text is the elements joined by newlines (``page_layout``'s own
    page text). The selection is looked up verbatim first (``find_verbatim``:
    whitespace-collapsed, case-insensitive → ``matched_by: "verbatim"``), then
    under ``find_normalised`` (soft hyphens, line-wrap hyphens, ligatures,
    curly quotes, non-breaking spaces → ``matched_by: "normalised"``) so the
    UI can say a repair happened. The elements whose character range overlaps
    the match are the answer, the union of their relative boxes the bbox —
    both exact, from the stored positions. ``page`` restricts the search to
    one page (1-based); without it pages are searched in order and the first
    hit wins. Not found anywhere: ``found: False``, ``matched_by: None``, no
    ids — never the nearest element, never an edit-distance guess.
    """
    empty = {"element_ids": [], "page": page, "bbox_rel": None, "found": False, "matched_by": None}
    needle = " ".join(str(text or "").split())
    if not needle or not is_source_jdf(jdf):
        return empty
    index = element_index(jdf, chunks)
    if not index:
        return empty
    by_page: dict[int, list[tuple[str, dict]]] = {}
    for eid, info in index.items():
        by_page.setdefault(int(info["page"]), []).append((eid, info))
    pages = [int(page)] if page is not None else sorted(by_page)
    for page_no in pages:
        entries = by_page.get(page_no) or []
        if not entries:
            continue
        entries.sort(key=lambda item: int(item[1].get("start") or 0))
        page_text = "\n".join(str(info["text"]) for _eid, info in entries)
        matched_by = "verbatim"
        span = find_verbatim(page_text, needle)
        if span is None:
            matched_by = "normalised"
            span = find_normalised(page_text, needle)
        if span is None:
            continue
        start, end = span
        hits = [
            (eid, info) for eid, info in entries
            if int(info.get("start") or 0) < end and int(info.get("end") or 0) > start
        ]
        if not hits:
            continue
        # A scanned page is one image element whose box is the page; the OCR
        # lines the match falls on give the box the reader actually wants
        # (Textract / tesseract output, 2026-09-29). Text-layer elements have
        # no lines and keep their own boxes.
        line_boxes: list[list[float] | None] = []
        for _eid, info in hits:
            base = int(info.get("start") or 0)
            for line in info.get("ocr_lines") or []:
                if base + line["start_char"] < end and base + line["end_char"] > start:
                    line_boxes.append(line.get("bbox"))
        bbox = _union_bbox(line_boxes) if line_boxes else _union_bbox([info.get("bbox_rel") for _eid, info in hits])
        return {
            "element_ids": [eid for eid, _info in hits],
            "page": page_no,
            "bbox_rel": bbox,
            "bbox_source": "ocr_lines" if line_boxes else "elements",
            "ocr_lines": len(line_boxes),
            "found": True,
            "matched_by": matched_by,
        }
    return empty


# --------------------------------------------------------------------------
# Page rasters (OCR documents)
# --------------------------------------------------------------------------

def _png_size(png: bytes) -> tuple[int | None, int | None]:
    """Width and height from the PNG header (IHDR at byte 16), no decode."""
    if len(png) >= 24 and png[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", png[16:24])
        return int(w), int(h)
    return None, None


def store_page_rasters(
    project_id: str,
    document_id: str,
    revision: str,
    file_bytes: bytes | None,
    filename: str | None,
    page_count: int,
    *,
    long_side_px: int = RASTER_LONG_SIDE_PX,
    max_pages: int = RASTER_MAX_PAGES,
) -> list[dict[str, Any]]:
    """Render and store one PNG per page of an OCR document; return what was stored.

    Each entry is ``{"page", "key", "url", "width", "height", "bytes", "ms"}``.
    The render is ``services/vision.render_page_png`` — the one PyMuPDF path
    (``quality_probe._open_document``) the visual probe and the vision pass
    already use, so a PDF page and an uploaded image render the same way and
    an image is never upscaled. Never raises: a page that does not render is
    logged and absent from the result, so the caller's ``rasters`` count is
    the number of pages the reader can actually see. Worker-side only: the
    web tier renders nothing (``docs/scale_architecture.md``).
    """
    if not file_bytes or page_count < 1:
        return []
    try:
        from .vision import render_page_png
    except ImportError:
        from vision import render_page_png  # type: ignore
    source_key = source_jdf_key(project_id, document_id, revision)
    store = get_object_store()
    out: list[dict[str, Any]] = []
    for page_no in range(1, min(int(page_count), max_pages) + 1):
        t0 = time.monotonic()
        try:
            png = render_page_png(file_bytes, filename or "", page_no, long_side_px=long_side_px)
            key = page_raster_key(source_key, page_no)
            store.put_bytes(key, png, content_type="image/png")
        except Exception:  # noqa: BLE001 — a page that does not render is reported by its absence
            log.exception("page raster %s/%s page %s could not be stored", project_id, document_id, page_no)
            continue
        width, height = _png_size(png)
        out.append({
            "page": page_no,
            "key": key,
            "url": page_raster_url(project_id, document_id, page_no),
            "width": width,
            "height": height,
            "bytes": len(png),
            "ms": int((time.monotonic() - t0) * 1000),
        })
    return out


def _page_size_mm(page: dict, doc: dict) -> tuple[float, float]:
    """The page's size in mm as jdf.js will lay it out: the page's
    ``pageSize``, else the document's, else A4 — the same fallback chain
    ``renderPage`` applies, so the raster covers exactly the drawn page."""
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    for size in (page.get("pageSize"), meta.get("pageSize")):
        if isinstance(size, dict):
            try:
                w, h = float(size.get("width")), float(size.get("height"))
            except (TypeError, ValueError):
                continue
            if w > 0 and h > 0:
                return w, h
        if isinstance(size, str) and size in _PAGE_SIZES_MM:
            w, h = _PAGE_SIZES_MM[size]
            orientation = str(page.get("pageOrientation") or meta.get("pageOrientation") or "portrait")
            return (h, w) if orientation == "landscape" else (w, h)
    return _PAGE_SIZES_MM["A4"]


def is_page_raster(el: Any) -> bool:
    return (
        isinstance(el, dict)
        and el.get("type") == "image"
        and isinstance(el.get("assure"), dict)
        and el["assure"].get("kind") == "page_raster"
    )


def add_page_rasters(doc: dict, rasters: list[dict[str, Any]] | None) -> int:
    """Put a full-page ``image`` element first on each rastered page, in place.

    ``position {x: 0, y: 0}``, ``width``/``height`` = the page size in mm,
    ``src`` = the raster route, ``fit: contain`` (jdf.js draws ``src`` URLs
    as-is and letter-boxes the bitmap into the box), ``assure: {kind:
    "page_raster", page, key, width_px, height_px}``. First so the text
    elements paint over it. Returns the number added; a page with no raster
    entry gets nothing, and an element already there is not duplicated.
    """
    if not rasters or not is_source_jdf(doc):
        return 0
    by_page = {int(r["page"]): r for r in rasters if isinstance(r, dict) and r.get("page") and r.get("url")}
    pages = [p for p in doc["pages"] if isinstance(p, dict)]
    added = 0
    for page_no, page in enumerate(pages, start=1):
        raster = by_page.get(page_no)
        if not raster:
            continue
        elements = page.get("elements")
        if not isinstance(elements, list):
            elements = page["elements"] = []
        if any(is_page_raster(el) for el in elements):
            continue
        width_mm, height_mm = _page_size_mm(page, doc)
        elements.insert(0, {
            "type": "image",
            "src": raster["url"],
            "alt": f"Page {page_no} as scanned",
            "position": {"x": 0, "y": 0},
            "width": width_mm,
            "height": height_mm,
            "fit": "contain",
            "assure": {
                "kind": "page_raster",
                "page": page_no,
                "key": raster.get("key"),
                "width_px": raster.get("width"),
                "height_px": raster.get("height"),
            },
        })
        added += 1
    return added


def count_page_rasters(doc: dict) -> int:
    if not is_source_jdf(doc):
        return 0
    return sum(1 for p in doc["pages"] if isinstance(p, dict) for el in (p.get("elements") or []) if is_page_raster(el))


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

def persist_source_jdf(
    project_id: str,
    document_id: str,
    revision: str,
    jdf: dict,
    *,
    chunks: list[dict] | None = None,
    job_id: str | None = None,
    filename: str | None = None,
    rasters: list[dict[str, Any]] | None = None,
    original: dict[str, Any] | None = None,
    analysis: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Store the raw jdf-cli document and return its descriptor, or None.

    The descriptor — ``{"key", "url", "pages", "elements", "rasters",
    "stored_at", "revision", "document_id"}`` — is what the ingest job row, the
    Parsure report (``report["source_jdf"]``) and the Assure tree
    (``meta.source_jdf``) record. ``rasters`` is what ``store_page_rasters``
    returned for an OCR document; each becomes a full-page ``image`` element
    (``add_page_rasters``) and ``descriptor["rasters"]`` counts them — 0 for a
    digital PDF, which gets none. Never raises: the revision is the deliverable and a store that is
    not reachable is logged and leaves ``source_jdf`` absent, which the UI
    reads as "no source document is stored". The document is copied before the
    ids are stamped so the in-memory bundle the rest of the pipeline reads is
    left as jdf-cli produced it.
    """
    if not is_source_jdf(jdf):
        return None
    try:
        doc = copy.deepcopy(jdf)
        stamped = stamp_element_ids(doc, chunks)
        # After stamping: the stamp walks text elements only, and the image
        # element carries no text, so the order does not matter for ids — but
        # stamping first keeps ``elements_stamped`` the count of text elements.
        rastered = add_page_rasters(doc, rasters)
        meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
        stored_at = _now()
        meta["assure"] = {
            "project_id": project_id,
            "document_id": document_id,
            "revision": revision,
            "filename": filename,
            "stored_at": stored_at,
            "element_id_policy": fx.NODE_ID_POLICY,
            "element_id_derivation": fx.NODE_ID_DERIVATION,
            "elements_stamped": stamped,
            "rasters": rastered,
        }
        if original:
            meta["assure"]["original"] = dict(original)
        if analysis:
            meta["assure"]["analysis"] = dict(analysis)
        doc["meta"] = meta
        key = source_jdf_key(project_id, document_id, revision)
        payload = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        get_object_store().put_bytes(key, payload, content_type="application/json")
        pages = [p for p in doc["pages"] if isinstance(p, dict)]
        descriptor = {
            "key": key,
            "url": source_jdf_url(project_id, document_id),
            "pages": len(pages),
            "elements": sum(len(_text_elements(p)) for p in pages),
            "rasters": rastered,
            "stored_at": stored_at,
            "revision": revision,
            "document_id": document_id,
            "bytes": len(payload),
        }
        if original:
            descriptor["original"] = {k: original[k] for k in ("url", "content_type", "display") if k in original}
        if analysis:
            descriptor["analysis"] = {k: analysis[k] for k in ("url", "pages", "fields", "tables") if k in analysis}
    except Exception:
        log.exception("source JDF for %s/%s could not be stored", project_id, document_id)
        return None
    if job_id:
        try:
            try:
                from ..db.ingest_jobs_repository import set_source_jdf_key
            except ImportError:
                from db.ingest_jobs_repository import set_source_jdf_key  # type: ignore
            set_source_jdf_key(job_id, key)
        except Exception:
            log.exception("ingest job %s: could not record source_jdf_key", job_id)
    return descriptor


def resolve_source_jdf_key(project_id: str, document_id: str, revision: str | None = None) -> str | None:
    """The stored key for a document of this project, newest first.

    Resolution: the project's ingest jobs (``source_jdf_key``, ordered by
    ``created_at``), then the project's intake reports (``report["source_jdf"]
    ["key"]``) for a Sources-panel upload that ran without a job (the
    synchronous path creates none). With ``revision`` the key must be that
    revision's. Every candidate is checked against the project prefix; a key
    that names another project is never returned.
    """
    prefix = document_prefix(project_id, document_id)
    wanted = source_jdf_key(project_id, document_id, revision) if revision else None

    def _accept(key: Any) -> str | None:
        k = str(key or "")
        if not k or not key_in_project(k, project_id) or not k.startswith(prefix):
            return None
        if wanted and k != wanted:
            return None
        return k

    try:
        try:
            from ..db.ingest_jobs_repository import list_source_jdf_keys
        except ImportError:
            from db.ingest_jobs_repository import list_source_jdf_keys  # type: ignore
        for key in list_source_jdf_keys(project_id, limit=_JOB_SCAN_LIMIT):
            hit = _accept(key)
            if hit:
                return hit
    except Exception:
        log.exception("source JDF: ingest job lookup failed for %s/%s", project_id, document_id)
    try:
        try:
            from ..db.parsure_repository import list_reports
        except ImportError:
            from db.parsure_repository import list_reports  # type: ignore
        for report in list_reports(project_id, limit=_JOB_SCAN_LIMIT, current_only=False) or []:
            if str(report.get("document_id") or "") != str(document_id):
                continue
            src = report.get("source_jdf")
            hit = _accept(src.get("key")) if isinstance(src, dict) else None
            if hit:
                return hit
    except Exception:
        log.exception("source JDF: report lookup failed for %s/%s", project_id, document_id)
    return None


def load_source_jdf(key: str) -> dict | None:
    """The stored document, parsed; None when the key is not in the store or
    does not hold a JDF. Reads what ``persist_source_jdf`` wrote — no parse."""
    try:
        raw = get_object_store().get_bytes(key)
    except Exception:
        return None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return doc if is_source_jdf(doc) else None


def describe_source_jdf(key: str, doc: dict) -> dict[str, Any]:
    """The ``source.json`` body for a stored document."""
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    assure = meta.get("assure") if isinstance(meta.get("assure"), dict) else {}
    pages = [p for p in doc["pages"] if isinstance(p, dict)]
    return {
        "key": key,
        "pages": len(pages),
        "elements": sum(len(_text_elements(p)) for p in pages),
        "rasters": count_page_rasters(doc),
        "stored_at": assure.get("stored_at"),
        "revision": assure.get("revision"),
        "element_id_policy": assure.get("element_id_policy"),
        **({"original": {k: assure["original"][k] for k in ("url", "content_type", "display") if k in assure["original"]}}
           if isinstance(assure.get("original"), dict) else {}),
        **({"analysis": {k: assure["analysis"][k] for k in ("url", "pages", "fields", "tables") if k in assure["analysis"]}}
           if isinstance(assure.get("analysis"), dict) else {}),
    }



def descriptor_for_document(project_id: str, document_id: str) -> dict[str, Any] | None:
    """The stored descriptor (``{key, url, pages, elements, stored_at, …}``) of
    a document's latest source JDF, or None when none is stored.

    The key comes from ``resolve_source_jdf_key`` (ingest jobs, then reports);
    the descriptor is the one the intake report recorded for that key
    (``report["source_jdf"]``), and, for a key no report carries, is read from
    the stored document's own ``meta.assure`` — the JSON this service wrote,
    not a parse. None is never replaced by a guessed URL.
    """
    key = resolve_source_jdf_key(project_id, document_id)
    if not key:
        return None
    try:
        try:
            from ..db.parsure_repository import list_reports
        except ImportError:
            from db.parsure_repository import list_reports  # type: ignore
        for report in list_reports(project_id, limit=_JOB_SCAN_LIMIT, current_only=False) or []:
            src = report.get("source_jdf")
            if isinstance(src, dict) and str(src.get("key") or "") == key:
                return dict(src)
    except Exception:
        log.exception("source JDF: report lookup failed for %s/%s", project_id, document_id)
    doc = load_source_jdf(key)
    if doc is None:
        return None
    info = describe_source_jdf(key, doc)
    return {"url": source_jdf_url(project_id, document_id), "document_id": document_id, **info}


def descriptors_for_rows(project_id: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The stored descriptors of the Sources rows a compile ran with, in the
    rows' order (the ranked, cited order), skipping rows with none. A row's
    ``id`` is the ``document_id`` the Sources upload stored under
    (``routers/substrate.ingest_substrate_file``)."""
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("id") in (None, ""):
            continue
        try:
            desc = descriptor_for_document(project_id, str(row["id"]))
        except Exception:  # noqa: BLE001 — a lookup failure is "none stored", logged
            log.exception("source JDF: descriptor lookup failed for row %s", row.get("id"))
            desc = None
        if desc:
            out.append(desc)
    return out

# --------------------------------------------------------------------------
# Selection anchors (compile / inquire passthrough)
# --------------------------------------------------------------------------

class SelectionAnchor(BaseModel):
    """A text selection the browser made on the rendered source JDF.

    Strict: a page that arrives as ``"3"`` or element ids that are not strings
    are a client bug and are refused with 400 rather than coerced — the anchor
    is recorded on the document and a coerced value would be a value the
    client never sent.
    """

    model_config = ConfigDict(extra="ignore", strict=True)

    text: str = Field(min_length=1, max_length=SELECTION_TEXT_MAX_CHARS)
    page: int | None = None
    element_ids: list[str] = Field(default_factory=list, max_length=500)
    document_id: str | None = None
    source_jdf: str | None = None


def selection_anchor_meta(selection: dict[str, Any] | SelectionAnchor, source_texts: list[str]) -> dict[str, Any]:
    """The ``meta.selection_anchor`` block: the selection as sent plus
    ``verbatim`` — whether the text was re-found (whitespace-collapsed,
    case-insensitive) in one of the cited sources' ``extracted_text``. The
    browser's claim that the text came from the page is not taken on trust;
    without a source that carries it the anchor says ``verbatim: false``.
    """
    data = selection.model_dump() if isinstance(selection, SelectionAnchor) else dict(selection or {})
    text = str(data.get("text") or "")
    verbatim = any(find_verbatim(str(src or ""), text) is not None for src in source_texts) if text.strip() else False
    return {
        "text": text,
        "page": data.get("page"),
        "element_ids": [str(e) for e in (data.get("element_ids") or [])],
        "document_id": data.get("document_id"),
        "source_jdf": data.get("source_jdf"),
        "verbatim": bool(verbatim),
        "checked_against": len(source_texts),
    }
