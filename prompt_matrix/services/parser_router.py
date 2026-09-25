"""Parser routing: the one place that decides which parser backend parses a document.

``select_parser`` only decides the backend — it does not execute the parse,
handle direct-text ingestion, or own fallback behavior. Those stay in the
callers/ingest helpers, which execute the decision rather than make it:

- ``"jdf"``      → JDF CI (``services/jdf_converter.pdf_to_parse_bundle``)
- ``"jdf-ocr"``  → JDF CI with OCR for a scan (``pdf_to_parse_bundle(ocr=...)``;
                   the caller falls back to Textract if jdf-cli fails)
- ``"textract"`` → AWS Textract (``lib/textract.TextractClient``)

Caller rule for a text-like file (extension in the text set, or
``source_kind="text"``): the ``"jdf"`` return here signals *wrap the text
content directly as a JDF document* — the caller skips binary parsing
entirely (no ``pdf_to_parse_bundle``, no Textract) and never probes the file
as PDF. That is execution of the routing decision, not a second router.

Images (png/jpg/jpeg/tif/tiff/bmp) have no text layer by definition, so
they go to the scan backend without a probe (spec §2: one multimodal router).

``route_intake`` is the one authoritative multimodal intake (spec §2, §8
rule 1): it wraps ``select_parser`` with material detection, the visual
quality probe and Laya's rule-based triage (``services/laya``), which is
called from here and nowhere else. Laya annotates; it never overrides
``select_parser``.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

log = logging.getLogger(__name__)

ParserName = Literal["jdf", "jdf-ocr", "textract"]

#: What parses a scan. ``jdf-ocr`` runs jdf-cli with its bundled tesseract.js
#: (local, no per-page fee); ``textract`` is Amazon Textract (per-page fee,
#: AWS credentials). ``PARSER_SCAN_BACKEND`` overrides; ``JDF_OCR=none`` also
#: forces Textract, because jdf-cli without OCR would return empty pages.
_SCAN_BACKEND_DEFAULT = "jdf-ocr"

#: Extensions whose content is already text — the caller wraps it as a JDF
#: document directly, so no binary probing of any kind runs.
_TEXT_LIKE_EXTENSIONS = frozenset(
    {"txt", "md", "json", "csv", "rst", "yaml", "yml"}
)

#: Raster formats an upload may carry (PyMuPDF opens all of them). No text
#: layer is possible, so the scan backend reads them.
_IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "tif", "tiff", "bmp"})

#: Pages the PDF probe reads before deciding a PDF has no text layer. Scans
#: usually have no text on any page; three is enough to tell, cheaply.
_PROBE_PAGE_LIMIT = 3


def select_parser(
    file_bytes: bytes,
    filename: str | None = None,
    *,
    source_kind: str | None = None,
) -> ParserName:
    """Decide which parser backend to use.

    Returns:
        "jdf" — use JDF CI (``pdf_to_parse_bundle`` in jdf_converter.py).
        "jdf-ocr" — a scan: JDF CI with OCR (default), see ``scan_backend``.
        "textract" — use AWS Textract (``lib/textract.TextractClient``).

    ``source_kind`` (optional override):
        "scanned" → force the scan backend (the client knows the document is
        scanned/image-only).
        "text" → return "jdf" *only to signal* "this is not binary: the
        caller wraps the text content directly as a JDF document — do NOT
        probe or run binary parsing".

    ``source_kind="pdf"`` is not a valid value and must not be used: the
    default behavior (no ``source_kind``, or any unrecognized value) is to
    probe and decide.
    """
    # 1. Client override takes precedence.
    if source_kind == "scanned":
        return scan_backend()
    if source_kind == "text":
        # Caller wraps text as JDF — skip binary parsing entirely.
        return "jdf"

    # 2. Filename hint for text-like files — skip binary probing entirely.
    #    Images: no text layer can exist, so the scan backend, no probe.
    if filename:
        ext = filename.lower().split(".")[-1]
        if ext in _TEXT_LIKE_EXTENSIONS:
            return "jdf"  # text-like: caller wraps content as JDF, no probe
        if ext in _IMAGE_EXTENSIONS:
            return scan_backend()

    # 3. PDF probe (only for .pdf files).
    if filename and filename.lower().endswith(".pdf"):
        return _probe_pdf_for_parser(file_bytes)

    # 4. Default: try JDF CI.
    return "jdf"


def is_image_filename(filename: str | None) -> bool:
    """True when the extension names a raster format the router sends to OCR."""
    if not filename or "." not in filename:
        return False
    return filename.lower().rsplit(".", 1)[-1] in _IMAGE_EXTENSIONS


def route_intake(
    file_bytes: bytes,
    filename: str | None = None,
    *,
    source_kind: str | None = None,
) -> dict:
    """The one authoritative multimodal intake decision (spec §2).

    Returns ``parser`` (exactly ``select_parser``'s answer — callers execute
    it as before), the detected ``material_type`` / ``modality`` /
    ``source_kind``, the per-page ``visual_pages`` from the quality probe
    (``[]`` when nothing renders — never synthetic pages) and Laya's
    rules-v1 ``laya`` annotation. The probe and Laya are observability and
    triage; a failure in either is logged and leaves ``parser`` untouched, so
    an intake never fails because its quality could not be measured.
    """
    try:
        from . import laya as _laya
        from .quality_probe import detect_material, probe_visual_quality
    except ImportError:
        import laya as _laya  # type: ignore[no-redef]
        from quality_probe import detect_material, probe_visual_quality  # type: ignore[no-redef]

    parser = select_parser(file_bytes, filename, source_kind=source_kind)
    name = filename or ""
    try:
        material = detect_material(file_bytes, name, source_kind=source_kind)
    except Exception:
        log.exception("route_intake: material detection failed for %s", name)
        material = {"material_type": None, "modality": None, "source_kind": source_kind, "basis": "detection failed"}
    try:
        visual_pages = probe_visual_quality(file_bytes, name)
    except Exception:
        log.exception("route_intake: visual probe failed for %s", name)
        visual_pages = []
    try:
        triage = _laya.triage(
            material_type=material.get("material_type"),
            modality=material.get("modality"),
            visual_pages=visual_pages,
            parser=parser,
        )
    except Exception:
        log.exception("route_intake: laya triage failed for %s", name)
        triage = None
    return {
        "parser": parser,
        "material_type": material.get("material_type"),
        "modality": material.get("modality"),
        "source_kind": material.get("source_kind"),
        "material_basis": material.get("basis"),
        "visual_pages": visual_pages,
        "laya": triage,
    }


def scan_backend() -> ParserName:
    """The backend a document with no text layer goes to."""
    override = (os.environ.get("PARSER_SCAN_BACKEND") or "").strip().lower()
    if override in ("textract", "jdf-ocr"):
        return override  # type: ignore[return-value]
    if (os.environ.get("JDF_OCR") or "").strip().lower() == "none":
        return "textract"
    return _SCAN_BACKEND_DEFAULT  # type: ignore[return-value]


def _probe_pdf_for_parser(file_bytes: bytes) -> ParserName:
    """Cheap probe using PyMuPDF (fitz) — already a dependency.

    Checks the first pages for extractable text: a PDF whose opening pages
    carry no text layer is a scan, and JDF CI is a text-layer parser, so it
    would return empty pages — that document belongs to Textract. A probe
    failure defaults to JDF and lets the parse fail naturally (the caller's
    existing fallback handles it), rather than guessing "scanned" from a
    corrupt file.
    """
    import fitz

    try:
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            for page_num in range(min(_PROBE_PAGE_LIMIT, len(doc))):
                page = doc[page_num]
                if page.get_text().strip():
                    return "jdf"
            return scan_backend()  # no text found → a scan
    except Exception:
        return "jdf"  # probe failed → default to JDF, let parse fail naturally