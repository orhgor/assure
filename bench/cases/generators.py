"""Build the synthetic benchmark inputs with PyMuPDF (``bench-v1``).

Each public generator returns ``(filename, bytes, meta)``. ``meta`` records
the rendering facts a reader needs to interpret a result — DPI, rotation,
JPEG quality, which font the "handwriting" used (or that no script font was
found and the case is not extractable by design). The manifest names
generators by function name (``GENERATORS[name]``).

Rendering is deterministic apart from PyMuPDF's PDF trailer ``/ID`` and
timestamps, which the page content does not depend on; the benchmark
compares what the pipeline read, not the container bytes.

Why PyMuPDF and not a fixture directory: the cases must be reproducible on a
machine that has never seen a carrier document, and a rotated / low-contrast
/ low-DPI variant of the same page isolates one degradation at a time.
"""

from __future__ import annotations

import os
from typing import Any, Callable

import fitz  # PyMuPDF

try:
    from . import texts as T
except ImportError:  # bench/ used as a plain directory on sys.path
    import texts as T  # type: ignore[no-redef]

Generated = tuple[str, bytes, dict[str, Any]]

PAGE_W, PAGE_H = 612, 792  # US Letter, points
MARGIN_X, TOP_Y = 54, 64
BODY_FONT = "helv"
BODY_SIZE = 10.0
LINE_H = 13.5

#: Script fonts tried, in order, for the handwritten cases. macOS ships the
#: first two; a Linux CI box usually has none, in which case the generator
#: falls back to Helvetica and marks the case ``not_extractable_by_design``
#: (the manifest's expected fields are then reported, not gated).
SCRIPT_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",
    "/System/Library/Fonts/Supplemental/Brush Script.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
)


def _new_doc() -> fitz.Document:
    doc = fitz.open()
    doc.set_metadata({})
    return doc


def _write_lines(page: fitz.Page, text: str, *, y: float = TOP_Y, fontname: str = BODY_FONT, fontsize: float = BODY_SIZE,
                 color: tuple[float, float, float] = (0, 0, 0), x: float = MARGIN_X) -> float:
    """Write ``text`` line by line; returns the y after the last line.

    Paragraphs (blank-line separated) are written as separate text objects so
    the text layer keeps the same block structure a typed form has.
    """
    for para in text.split("\n\n"):
        lines = para.split("\n")
        page.insert_text((x, y), "\n".join(lines), fontname=fontname, fontsize=fontsize, color=color, lineheight=LINE_H / fontsize)
        y += LINE_H * len(lines) + LINE_H * 0.6
    return y


def _typed_pdf(text: str, *, sign_stroke: bool | None = None) -> bytes:
    doc = _new_doc()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    _write_lines(page, text)
    if sign_stroke is not None:
        _draw_signature_line(page, text, stroke=sign_stroke)
    return doc.tobytes(deflate=True, garbage=3)


def _draw_signature_line(page: fitz.Page, text: str, *, stroke: bool) -> None:
    """A ruled signature line under the "Authorized Signature" label and,
    with ``stroke``, an ink-like polyline across it. The stroke is vector
    ink, not text: no text layer says a signature is there, exactly as on a
    wet-signed page that was later scanned."""
    hits = page.search_for("Authorized Signature")
    if not hits:
        return
    rect = hits[0]
    x0, y = rect.x1 + 6, rect.y1 - 1
    page.draw_line((x0, y), (x0 + 220, y), color=(0, 0, 0), width=0.6)
    if stroke:
        pts = []
        for i in range(0, 41):
            t = i / 40
            xx = x0 + 8 + t * 180
            yy = y - 4 - 9 * abs(((t * 6) % 2) - 1) - (6 if 0.35 < t < 0.55 else 0)
            pts.append((xx, yy))
        page.draw_polyline(pts, color=(0.05, 0.05, 0.25), width=1.6)
        page.draw_bezier((x0 + 10, y - 2), (x0 + 40, y - 26), (x0 + 90, y + 6), (x0 + 130, y - 14), color=(0.05, 0.05, 0.25), width=1.4)


def _rasterise(pdf_bytes: bytes, *, dpi: int, rotate: int = 0, fmt: str = "png", jpg_quality: int = 95) -> list[bytes]:
    """One image per page at ``dpi``; ``rotate`` is applied to the pixels
    (the way a scanner or a phone held sideways rotates the page)."""
    out = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            mat = fitz.Matrix(dpi / 72, dpi / 72).prerotate(rotate)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            out.append(pix.tobytes(fmt, jpg_quality=jpg_quality) if fmt == "jpeg" else pix.tobytes(fmt))
    return out


def _image_only_pdf(images: list[bytes], *, page_size: tuple[float, float]) -> bytes:
    """Wrap page images in a PDF with no text layer (a scan)."""
    doc = _new_doc()
    w, h = page_size
    for img in images:
        page = doc.new_page(width=w, height=h)
        page.insert_image(page.rect, stream=img)
    return doc.tobytes(deflate=True, garbage=3)


def _script_font() -> str | None:
    for path in SCRIPT_FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


# --------------------------------------------------------------------------- #
# policies
# --------------------------------------------------------------------------- #

def auto_declarations_typed() -> Generated:
    return "auto_declarations.pdf", _typed_pdf(T.AUTO_DECLARATIONS), {"modality": "digital_pdf"}


def property_declarations_typed() -> Generated:
    return "homeowners_declarations.pdf", _typed_pdf(T.PROPERTY_DECLARATIONS), {"modality": "digital_pdf"}


def auto_endorsement_typed() -> Generated:
    return "auto_endorsement_END-04.pdf", _typed_pdf(T.AUTO_ENDORSEMENT), {"modality": "digital_pdf"}


def auto_cancellation_typed() -> Generated:
    return "auto_cancellation_notice.pdf", _typed_pdf(T.AUTO_CANCELLATION), {"modality": "digital_pdf"}


def auto_renewal_wrong_policy_number_typed() -> Generated:
    return "auto_renewal_declarations.pdf", _typed_pdf(T.AUTO_RENEWAL_WRONG_POLICY_NUMBER), {"modality": "digital_pdf"}


# --------------------------------------------------------------------------- #
# claims
# --------------------------------------------------------------------------- #

def auto_fnol_typed() -> Generated:
    return "auto_fnol.pdf", _typed_pdf(T.AUTO_FNOL), {"modality": "digital_pdf"}


def auto_fnol_wrong_vin_typed() -> Generated:
    return "auto_fnol_wrong_vin.pdf", _typed_pdf(T.AUTO_FNOL_WRONG_VIN), {"modality": "digital_pdf"}


def auto_fnol_misspelled_insured_typed() -> Generated:
    return "auto_fnol_misspelled.pdf", _typed_pdf(T.AUTO_FNOL_MISSPELLED_INSURED), {"modality": "digital_pdf"}


def cms1500_typed() -> Generated:
    return "cms1500.pdf", _typed_pdf(T.CMS_1500), {"modality": "digital_pdf"}


def property_claim_typed() -> Generated:
    return "property_loss_notice.pdf", _typed_pdf(T.PROPERTY_CLAIM), {"modality": "digital_pdf"}


def _draw_table(page: fitz.Page, y: float, header: tuple[str, ...], rows: tuple[tuple[str, ...], ...], col_x: tuple[float, ...],
                right_align: tuple[int, ...] = ()) -> float:
    """A ruled table with a header row; returns the y below the table."""
    row_h = 16.0
    x_end = PAGE_W - MARGIN_X
    page.draw_rect(fitz.Rect(MARGIN_X, y - 12, x_end, y + 4), color=(0, 0, 0), fill=(0.9, 0.9, 0.9), width=0.5)
    for i, cell in enumerate(header):
        page.insert_text((col_x[i] + 3, y), cell, fontname="hebo", fontsize=8.5)
    y += row_h
    for row in rows:
        for i, cell in enumerate(row):
            if i in right_align:
                w = fitz.get_text_length(cell, fontname=BODY_FONT, fontsize=8.5)
                nxt = col_x[i + 1] if i + 1 < len(col_x) else x_end
                page.insert_text((nxt - 3 - w, y), cell, fontname=BODY_FONT, fontsize=8.5)
            else:
                page.insert_text((col_x[i] + 3, y), cell, fontname=BODY_FONT, fontsize=8.5)
        page.draw_line((MARGIN_X, y + 4), (x_end, y + 4), color=(0.5, 0.5, 0.5), width=0.3)
        y += row_h
    for x in col_x + (x_end,):
        page.draw_line((x, y - row_h * (len(rows) + 1) - 12), (x, y - row_h + 4), color=(0.5, 0.5, 0.5), width=0.3)
    return y + 8


def repair_estimate_table_typed() -> Generated:
    doc = _new_doc()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    y = _write_lines(page, T.REPAIR_ESTIMATE)
    y = _draw_table(page, y + 6, ("Part / Operation", "Op", "Hours", "Parts $", "Labor $"), T.REPAIR_ESTIMATE_ROWS,
                    (MARGIN_X, 300, 360, 420, 490), right_align=(2, 3, 4))
    _write_lines(page, "Parts subtotal: $2,139.00\nLabor subtotal: $1,479.00\nTax (6.25% on parts): $133.69\nSublet and misc: $523.31\n"
                 + T.REPAIR_ESTIMATE_TOTAL_LINE, y=y + 10)
    return "repair_estimate.pdf", doc.tobytes(deflate=True, garbage=3), {"modality": "digital_pdf", "table_rows": len(T.REPAIR_ESTIMATE_ROWS)}


# --------------------------------------------------------------------------- #
# photos
# --------------------------------------------------------------------------- #

def auto_declarations_phone_photo_72dpi() -> Generated:
    img = _rasterise(_typed_pdf(T.AUTO_DECLARATIONS), dpi=72, fmt="jpeg", jpg_quality=55)[0]
    return "IMG_4471.jpg", img, {"modality": "phone_photo", "dpi": 72, "jpeg_quality": 55}


def auto_declarations_phone_photo_110dpi() -> Generated:
    img = _rasterise(_typed_pdf(T.AUTO_DECLARATIONS), dpi=110, fmt="jpeg", jpg_quality=65)[0]
    return "IMG_4472.jpg", img, {"modality": "phone_photo", "dpi": 110, "jpeg_quality": 65}


def auto_fnol_screenshot_png() -> Generated:
    img = _rasterise(_typed_pdf(T.AUTO_FNOL), dpi=96)[0]
    return "Screenshot 2025-08-15 at 09.12.44.png", img, {"modality": "screenshot", "dpi": 96}


def property_declarations_scan_150dpi() -> Generated:
    imgs = _rasterise(_typed_pdf(T.PROPERTY_DECLARATIONS), dpi=150)
    return "homeowners_declarations_scan.pdf", _image_only_pdf(imgs, page_size=(PAGE_W, PAGE_H)), {"modality": "scanned_pdf", "dpi": 150}


# --------------------------------------------------------------------------- #
# signatures
# --------------------------------------------------------------------------- #

def auto_declarations_signed_stroke_scan() -> Generated:
    pdf = _typed_pdf(T.AUTO_DECLARATIONS_BLANK_SIGNATURE, sign_stroke=True)
    imgs = _rasterise(pdf, dpi=200)
    return "auto_declarations_signed.pdf", _image_only_pdf(imgs, page_size=(PAGE_W, PAGE_H)), {"modality": "scanned_pdf", "dpi": 200, "ink": "vector stroke, rasterised"}


def auto_declarations_blank_signature_scan() -> Generated:
    pdf = _typed_pdf(T.AUTO_DECLARATIONS_BLANK_SIGNATURE, sign_stroke=False)
    imgs = _rasterise(pdf, dpi=200)
    return "auto_declarations_unsigned.pdf", _image_only_pdf(imgs, page_size=(PAGE_W, PAGE_H)), {"modality": "scanned_pdf", "dpi": 200, "ink": "none"}


def auto_declarations_signed_stroke_typed() -> Generated:
    """Text layer + a vector ink stroke: the born-digital form that was
    signed on a tablet. Separates "can we see ink" from "can we read text"."""
    return "auto_declarations_esigned.pdf", _typed_pdf(T.AUTO_DECLARATIONS_BLANK_SIGNATURE, sign_stroke=True), {"modality": "digital_pdf", "ink": "vector stroke"}


# --------------------------------------------------------------------------- #
# handwritten
# --------------------------------------------------------------------------- #

def adjuster_note_handwritten_scan() -> Generated:
    font = _script_font()
    doc = _new_doc()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    meta: dict[str, Any] = {"modality": "scanned_pdf", "dpi": 200}
    if font:
        page.insert_font(fontname="hand", fontfile=font)
        _write_lines(page, T.ADJUSTER_NOTE, fontname="hand", fontsize=15, color=(0.1, 0.1, 0.35))
        meta["script_font"] = os.path.basename(font)
    else:
        _write_lines(page, T.ADJUSTER_NOTE)
        meta["script_font"] = None
        meta["not_extractable_by_design"] = True
    imgs = _rasterise(doc.tobytes(), dpi=200)
    return "adjuster_note_scan.pdf", _image_only_pdf(imgs, page_size=(PAGE_W, PAGE_H)), meta


def fnol_form_handwritten_values_scan() -> Generated:
    """Typed labels, handwritten answers — the most common real mix."""
    font = _script_font()
    doc = _new_doc()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    meta: dict[str, Any] = {"modality": "scanned_pdf", "dpi": 200}
    y = _write_lines(page, T.FNOL_FORM_TITLE)
    if font:
        page.insert_font(fontname="hand", fontfile=font)
        meta["script_font"] = os.path.basename(font)
    else:
        meta["script_font"] = None
        meta["not_extractable_by_design"] = True
    y += 6
    for label, value in T.FNOL_FORM_LABELS_AND_VALUES:
        page.insert_text((MARGIN_X, y), label, fontname=BODY_FONT, fontsize=BODY_SIZE)
        page.draw_line((MARGIN_X + 150, y + 2), (PAGE_W - MARGIN_X, y + 2), color=(0.6, 0.6, 0.6), width=0.4)
        page.insert_text((MARGIN_X + 156, y - 1), value, fontname="hand" if font else BODY_FONT, fontsize=14 if font else BODY_SIZE, color=(0.1, 0.1, 0.35))
        y += 30
    imgs = _rasterise(doc.tobytes(), dpi=200)
    return "fnol_handwritten_scan.pdf", _image_only_pdf(imgs, page_size=(PAGE_W, PAGE_H)), meta


# --------------------------------------------------------------------------- #
# mixed / noisy
# --------------------------------------------------------------------------- #

def bundle_policy_and_fnol_typed() -> Generated:
    """Two documents in one PDF: the declarations page split over two pages
    (header + coverages) and the FNOL on page 3."""
    head, cov = T.AUTO_DECLARATIONS.split("COVERAGES AND LIMITS\n", 1)
    doc = _new_doc()
    p1 = doc.new_page(width=PAGE_W, height=PAGE_H)
    _write_lines(p1, head.rstrip() + "\n\nPage 1 of 2")
    p2 = doc.new_page(width=PAGE_W, height=PAGE_H)
    _write_lines(p2, "COVERAGES AND LIMITS (continued)\n" + cov.rstrip() + "\n\nPage 2 of 2")
    p3 = doc.new_page(width=PAGE_W, height=PAGE_H)
    _write_lines(p3, T.AUTO_FNOL)
    return "policy_and_claim_bundle.pdf", doc.tobytes(deflate=True, garbage=3), {"modality": "digital_pdf", "documents": 2, "pages": 3}


def auto_declarations_rotated_scan_90() -> Generated:
    imgs = _rasterise(_typed_pdf(T.AUTO_DECLARATIONS), dpi=150, rotate=90)
    return "auto_declarations_rotated.pdf", _image_only_pdf(imgs, page_size=(PAGE_H, PAGE_W)), {"modality": "scanned_pdf", "dpi": 150, "rotation": 90}


def auto_declarations_low_contrast_scan() -> Generated:
    doc = _new_doc()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.draw_rect(page.rect, color=None, fill=(0.82, 0.82, 0.80))
    _write_lines(page, T.AUTO_DECLARATIONS, color=(0.58, 0.58, 0.58))
    imgs = _rasterise(doc.tobytes(), dpi=150)
    return "auto_declarations_faded.pdf", _image_only_pdf(imgs, page_size=(PAGE_W, PAGE_H)), {"modality": "scanned_pdf", "dpi": 150, "contrast": "text 0.58 grey on 0.82 grey"}


def auto_declarations_partial_page_scan() -> Generated:
    """The top half of the page only (torn / mis-fed sheet): the labels below
    the cut are not on the document, so they are not expected."""
    with fitz.open(stream=_typed_pdf(T.AUTO_DECLARATIONS), filetype="pdf") as doc:
        page = doc[0]
        clip = fitz.Rect(0, 0, PAGE_W, PAGE_H / 2)
        pix = page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72), clip=clip, alpha=False)
        img = pix.tobytes("png")
    return "auto_declarations_partial.pdf", _image_only_pdf([img], page_size=(PAGE_W, PAGE_H / 2)), {"modality": "scanned_pdf", "dpi": 150, "kept": "top half"}


def coverage_schedule_table_typed() -> Generated:
    doc = _new_doc()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    y = _write_lines(page, T.COVERAGE_SCHEDULE_HEADER)
    y = _draw_table(page, y + 6, ("Coverage", "Form", "Limit", "Deductible", "Premium"), T.COVERAGE_SCHEDULE_ROWS,
                    (MARGIN_X, 250, 330, 410, 490), right_align=(2, 3, 4))
    _write_lines(page, T.COVERAGE_SCHEDULE_FOOTER, y=y + 10)
    return "coverage_schedule.pdf", doc.tobytes(deflate=True, garbage=3), {"modality": "digital_pdf", "table_rows": len(T.COVERAGE_SCHEDULE_ROWS)}


GENERATORS: dict[str, Callable[[], Generated]] = {
    fn.__name__: fn
    for fn in (
        auto_declarations_typed, property_declarations_typed, auto_endorsement_typed, auto_cancellation_typed,
        auto_renewal_wrong_policy_number_typed,
        auto_fnol_typed, auto_fnol_wrong_vin_typed, auto_fnol_misspelled_insured_typed, cms1500_typed, property_claim_typed,
        repair_estimate_table_typed,
        auto_declarations_phone_photo_72dpi, auto_declarations_phone_photo_110dpi, auto_fnol_screenshot_png, property_declarations_scan_150dpi,
        auto_declarations_signed_stroke_scan, auto_declarations_blank_signature_scan, auto_declarations_signed_stroke_typed,
        adjuster_note_handwritten_scan, fnol_form_handwritten_values_scan,
        bundle_policy_and_fnol_typed, auto_declarations_rotated_scan_90, auto_declarations_low_contrast_scan,
        auto_declarations_partial_page_scan, coverage_schedule_table_typed,
    )
}


def build(name: str) -> Generated:
    """Run the generator called ``name``; ``KeyError`` for an unknown name."""
    return GENERATORS[name]()
