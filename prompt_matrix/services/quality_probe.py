"""Visual quality probe and quality-weighted confidence (spec §4, §6).

Every number this module emits is measured from the bytes it was given or is
``None``; nothing is guessed. The probe renders each page to a small grayscale
sample with PyMuPDF and does the pixel arithmetic in pure Python (no Pillow,
no numpy — neither is a dependency of the image), so the sample is capped at
``SAMPLE_LONG_SIDE_PX``, ratios are counted with a single C pass
(``bytes.translate`` + ``count``) and the Laplacian runs on a small 150-dpi
crop, every other row. Measured 2026-09-25 on an M-series laptop: 50 text
pages ≈ 0.7 s, 50 raster pages ≈ 0.9 s.

What is measured in V1:

- ``blur_variance`` — variance of the 4-neighbour Laplacian (the classic
  "variance of Laplacian" focus measure) over a 150-dpi crop of the densest
  text band. Crisp text has strong edges and a high variance; a soft raster
  (out-of-focus photo) a low one. A whole-page sample small enough for pure
  Python cannot see this — it is itself ~40 dpi.
- ``contrast_std`` — standard deviation of the gray histogram (0–255),
  reported as measured; ``contrast_range`` (background minus ink level) is
  what the ``low_contrast`` flag uses, because the std of a mostly-paper page
  is small however black its text is.
- ``dpi_estimate`` — for a PDF page: the largest embedded raster's pixel width
  divided by the width it is drawn at in inches (a real effective DPI); for a
  vector-only page ``None`` (vector text has no DPI). For an image file: the
  file's own resolution metadata when it is not the meaningless defaults
  72/96, otherwise the long side divided by 11 in, i.e. *assuming a
  letter/A4 page* — the basis string says which.
- ``ink_ratio`` / ``bottom_ink_ratio`` — fraction of dark pixels on the page
  and in its bottom 20 %, the inputs :func:`assess_signature` uses.

What is NOT detected in V1 (never emitted, so a consumer can trust that the
absence of the flag is not a claim of quality): ``skewed``, ``glare``,
``noisy``. Likewise :func:`detect_material` never emits ``handwritten_image``,
``table`` or ``mixed_bundle`` from the bytes — only from an explicit client
``source_kind`` hint — because no signal in this module can tell them apart
from an ordinary scan.

Thresholds are *initial calibration values*, measured on the synthetic
fixtures in ``tests/test_quality_probe.py`` (crisp 11-pt text rendered by
PyMuPDF, the same page rendered at 40 dpi and re-embedded, and gray text on a
light-gray page). They separate those fixtures with margin; they have not yet
been tuned on client scans and should be revisited when real material with
known outcomes is available.
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Calibration constants (see module docstring for how they were chosen)
# --------------------------------------------------------------------------- #

#: Below this effective resolution a scan is "low_res" (spec §6: < 200 DPI
#: route to the OCR backend).
LOW_RES_DPI = 200.0

#: Variance-of-Laplacian floor, measured on the 150-dpi crop of the densest
#: text band (see ``_blur_crop``). Fixture values (2026-09-25): crisp 11-pt
#: vector text ≈ 13 000–16 000; the same page as a 40–300 dpi raster
#: 1 300–15 000 (hard pixel edges, not blur — that is the ``low_res`` case);
#: a soft raster (rendered, scaled down to 15–40 dpi and smoothly back up,
#: i.e. an out-of-focus photo) 4–190. The Laplacian scales with contrast²,
#: so a crisp low-contrast page also measures low (gray 0.7 on 0.9 ≈ 590):
#: ``blurry`` is therefore only raised when the contrast-normalised variance
#: ``blur × (255 / contrast_range)²`` is also under the floor.
BLUR_VARIANCE_MIN = 700.0

#: Gray-level standard deviation of the whole-page sample — reported as
#: measured, but NOT the low-contrast trigger: a crisp page with three lines
#: of text has a tiny std simply because it is mostly paper. Kept for the
#: contract field ``contrast_std``.
CONTRAST_STD_MIN = 20.0

#: Low-contrast trigger: background gray (median of the page) minus ink gray
#: (the darkest quarter of the marked pixels on the 100-dpi render), 0–255.
#: Measured 2026-09-25: black text on white ≈ 200–245 (three lines or a full
#: page alike), gray 0.55 text on 0.85 paper ≈ 72, gray 0.7 on 0.9 ≈ 51,
#: gray 0.5 on white ≈ 125, 0.35 on 0.95 ≈ 147.
CONTRAST_RANGE_MIN = 100.0

#: Resolution and size of the blur crop: 1.5 × 0.6 in of the densest text
#: band rendered at 150 dpi (≈ 225 × 90 px), enough to see edge softness
#: without a full-page 150-dpi raster the pure-Python loop could not afford.
BLUR_CROP_DPI = 150
BLUR_CROP_SIZE_IN = (1.5, 0.6)

#: A page whose marked-pixel fraction (≤ MARK_THRESHOLD, measured on the
#: 100-dpi render) is below this has nothing to measure (blank or near-blank).
#: Its numbers are still reported but it is not flagged: "low contrast" on a
#: blank separator page is not a quality problem. Measured: three lines of
#: 11-pt text ≈ 0.003, a full page ≈ 0.03–0.06.
BLANK_MARK_RATIO = 0.001

#: Resolution of the render the ink/mark ratios and the contrast ink level are
#: measured on. 100 dpi keeps 11-pt strokes ≥ 1 px so anti-aliasing does not
#: wash the ink out (at the 320-px sample it does: full-page text measured
#: ink 0.0016, below any sensible blank floor).
HIRES_DPI = 100

#: Gray level (0–255) at or below which a sample pixel counts as ink.
INK_THRESHOLD = 128

#: Gray level at or below which a pixel counts as *some* mark (light pencil,
#: faint pen). ``bottom_mark_ratio`` − ``bottom_ink_ratio`` is the faint part.
MARK_THRESHOLD = 200

#: Long side of the grayscale sample the probe analyses.
SAMPLE_LONG_SIDE_PX = 320

#: Fraction of the page height (from the bottom) treated as the signature
#: region by :func:`assess_signature`.
SIGNATURE_REGION_FRACTION = 0.20

#: Signature "faint" rule (spec §6: ink density under 30 % of typical).
#: Measured as *darkness* — the share of marked pixels that are ink
#: (≤ INK_THRESHOLD) — of the marks beyond the label line's own print,
#: against the body text's darkness. Fixtures 2026-09-25: a black 2.5-pt
#: stroke ≈ 0.9, body text ≈ 0.7, a 0.75-gray 1.5-pt stroke ≈ 0.0.
SIGNATURE_FAINT_FRACTION = 0.30

#: Marks in the signature region beyond what the label line itself accounts
#: for (estimated from its character share of the page text) must exceed
#: this fraction of the label's expected marks to count as "a mark exists".
SIGNATURE_LABEL_TOLERANCE = 0.25

#: Number readability rule from spec §6: OCR confidence under this is "faded".
NUMBER_FADED_OCR_MAX = 0.6

#: Page quality under this with acceptable OCR is "typewritten_low_quality".
NUMBER_LOW_PAGE_QUALITY_MAX = 0.5

NUMBER_PENALTIES: dict[str, float] = {
    "clear": 1.0,
    "printed_good": 1.0,
    "handwritten": 0.7,
    "faded": 0.6,
    "typewritten_low_quality": 0.8,
    "unreadable": 0.0,
    "unknown": 1.0,
}

SIGNATURE_PENALTIES: dict[str, float] = {
    "clear": 1.0,
    "faint": 0.8,
    "incomplete": 0.7,
    "stamped": 0.7,
    "questionable": 0.6,
    "missing": 0.5,
    "unknown": 1.0,
}

#: Z3 penalty for a field whose constraint was violated (spec §4 example 0.9).
Z3_VIOLATION_PENALTY = 0.9

#: Parser-confidence defaults when the parser reported none (spec §4).
PARSER_CONFIDENCE_DEFAULTS: dict[str, float] = {"jdf-cli": 0.85, "jdf": 0.85, "textract": 0.80, "jdf-cli+tesseract": 0.80}
PARSER_CONFIDENCE_FALLBACK = 0.5
PAGE_QUALITY_NEUTRAL = 0.5

IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "tif", "tiff", "bmp"})
TEXT_EXTENSIONS = frozenset({"txt", "md", "json", "csv", "rst", "yaml", "yml"})

#: Flags this module can emit. ``skewed``/``glare``/``noisy`` are in the
#: contract but NOT detected in V1 (see module docstring).
DETECTED_FLAGS = ("low_res", "blurry", "low_contrast")
UNDETECTED_FLAGS = ("skewed", "glare", "noisy")

_SIGNATURE_LABEL = re.compile(
    r"(authori[sz]ed\s+signature|signature|signed\s+by|signed|/s/)", re.IGNORECASE
)
_STAMP_WORDS = re.compile(r"\b(stamp(ed)?|seal|electronically\s+signed|e-?signed|docusign)\b", re.IGNORECASE)


def _ext(filename: str | None) -> str:
    return (filename or "").rsplit(".", 1)[-1].lower() if filename and "." in filename else ""


def _fitz():
    import fitz  # PyMuPDF — already a dependency (parser_router probe, pdf_import)

    return fitz


def _open_document(file_bytes: bytes, filename: str):
    """Open PDF or image bytes as a PyMuPDF document, or return ``None``.

    PyMuPDF opens PNG/JPEG/TIFF/BMP as one-page documents (no WebP: MuPDF 1.28
    ships without a WebP decoder, measured 2026-09-25), which is what
    lets the probe treat an uploaded photo exactly like a scanned page.
    """
    fitz = _fitz()
    ext = _ext(filename)
    filetype = "pdf" if ext == "pdf" or file_bytes[:5] == b"%PDF-" else (ext or None)
    if filetype in ("jpeg",):
        filetype = "jpg"
    try:
        if filetype:
            return fitz.open(stream=file_bytes, filetype=filetype)
        return fitz.open(stream=file_bytes)
    except Exception:
        return None


def image_to_pdf_bytes(file_bytes: bytes, filename: str) -> bytes:
    """A one-page PDF wrapping an image, for parsers that only read PDF.

    jdf-cli's OCR path takes a PDF; wrapping the raster losslessly (PyMuPDF
    ``convert_to_pdf`` embeds the pixels, it does not resample) keeps the
    image path on the same free OCR backend a scanned PDF uses. Raises
    ``ValueError`` when the bytes are not a renderable image.
    """
    doc = _open_document(file_bytes, filename)
    if doc is None:
        raise ValueError(f"not a renderable image: {filename}")
    try:
        return doc.convert_to_pdf()
    finally:
        doc.close()


def image_to_png_bytes(file_bytes: bytes, filename: str) -> bytes:
    """Re-encode an image as PNG (for a backend that does not read BMP)."""
    doc = _open_document(file_bytes, filename)
    if doc is None:
        raise ValueError(f"not a renderable image: {filename}")
    try:
        return doc[0].get_pixmap(alpha=False).tobytes("png")
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# Visual probe
# --------------------------------------------------------------------------- #


def _histogram(samples: bytes) -> list[int]:
    # 256 C-level ``bytes.count`` calls beat a Python loop over ~70k pixels.
    return [samples.count(bytes((v,))) for v in range(256)]


def _mean_std(hist: list[int], total: int) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    mean = sum(v * n for v, n in enumerate(hist)) / total
    var = sum(n * (v - mean) ** 2 for v, n in enumerate(hist)) / total
    return mean, var**0.5


def _percentile_gray(hist: list[int], total: int, fraction: float) -> int:
    """Gray level below which ``fraction`` of the pixels lie."""
    target = max(1, int(total * fraction))
    seen = 0
    for v, n in enumerate(hist):
        seen += n
        if seen >= target:
            return v
    return 255


def _laplacian_variance(samples: bytes, width: int, height: int) -> float | None:
    """Variance of the 4-neighbour Laplacian, every other row, pure Python."""
    if width < 3 or height < 3:
        return None
    s = samples
    total = 0
    total_sq = 0
    count = 0
    for y in range(1, height - 1, 2):
        row = y * width
        up = row - width
        down = row + width
        for x in range(1, width - 1):
            v = 4 * s[row + x] - s[row + x - 1] - s[row + x + 1] - s[up + x] - s[down + x]
            total += v
            total_sq += v * v
            count += 1
    if count == 0:
        return None
    mean = total / count
    return total_sq / count - mean * mean


_MASK_TABLES: dict[int, bytes] = {}


def _count_leq(segment: bytes, threshold: int) -> int:
    """Pixels with gray ≤ threshold — one C pass (translate + count), any size."""
    table = _MASK_TABLES.get(threshold)
    if table is None:
        table = bytes(1 if v <= threshold else 0 for v in range(256))
        _MASK_TABLES[threshold] = table
    return segment.translate(table).count(b"\x01")


def _percentile_gray_hires(segment: bytes, target_count: int) -> int:
    """Smallest gray level with at least ``target_count`` pixels at or below it (bisection)."""
    lo, hi = 0, 255
    while lo < hi:
        mid = (lo + hi) // 2
        if _count_leq(segment, mid) >= target_count:
            hi = mid
        else:
            lo = mid + 1
    return lo


def _region_ratios(samples: bytes, width: int, height: int) -> dict[str, float]:
    """Ink (≤ INK_THRESHOLD) and mark (≤ MARK_THRESHOLD) fractions: page, bottom 20 %, rest."""
    n = width * height
    keys = ("ink", "mark", "bottom_ink", "bottom_mark", "body_ink", "body_mark")
    if n == 0:
        return dict.fromkeys(keys, 0.0)
    split = int(height * (1.0 - SIGNATURE_REGION_FRACTION)) * width
    top, bottom = samples[:split], samples[split:]
    top_ink, top_mark = _count_leq(top, INK_THRESHOLD), _count_leq(top, MARK_THRESHOLD)
    bottom_ink, bottom_mark = _count_leq(bottom, INK_THRESHOLD), _count_leq(bottom, MARK_THRESHOLD)
    return {
        "ink": (top_ink + bottom_ink) / n,
        "mark": (top_mark + bottom_mark) / n,
        "bottom_ink": bottom_ink / len(bottom) if bottom else 0.0,
        "bottom_mark": bottom_mark / len(bottom) if bottom else 0.0,
        "body_ink": top_ink / len(top) if top else 0.0,
        "body_mark": top_mark / len(top) if top else 0.0,
    }


def _densest_window(samples: bytes, width: int, height: int, win_w: int, win_h: int) -> tuple[int, int] | None:
    """Top-left (x, y) of the ``win_w × win_h`` sample window with most ink, or None if no ink."""
    win_h = max(1, min(win_h, height))
    win_w = max(1, min(win_w, width))
    row_ink = [_count_leq(samples[y * width:(y + 1) * width], MARK_THRESHOLD) for y in range(height)]
    if not any(row_ink):
        return None
    best_y, best = 0, -1
    running = sum(row_ink[:win_h])
    for y in range(0, height - win_h + 1):
        if y:
            running += row_ink[y + win_h - 1] - row_ink[y - 1]
        if running > best:
            best, best_y = running, y
    col_ink = [0] * width
    for y in range(best_y, best_y + win_h):
        row = y * width
        for x in range(width):
            if samples[row + x] <= MARK_THRESHOLD:
                col_ink[x] += 1
    best_x, best = 0, -1
    running = sum(col_ink[:win_w])
    for x in range(0, width - win_w + 1):
        if x:
            running += col_ink[x + win_w - 1] - col_ink[x - 1]
        if running > best:
            best, best_x = running, x
    return best_x, best_y


def _blur_crop(page, samples: bytes, w: int, h: int, scale: float) -> tuple[float | None, str]:
    """Laplacian variance of the densest text band rendered at BLUR_CROP_DPI.

    A whole-page sample small enough for pure Python (~320 px) is itself a
    ~40-dpi image, so on it a 40-dpi scan and a 600-dpi one look identical
    (measured 2026-09-25: both ≈ 2750). Edge softness only shows at a
    resolution above the source's, hence a small crop at 150 dpi.
    """
    fitz = _fitz()
    px_per_in = scale * 72.0
    win_w = int(BLUR_CROP_SIZE_IN[0] * px_per_in)
    win_h = int(BLUR_CROP_SIZE_IN[1] * px_per_in)
    at = _densest_window(samples, w, h, win_w, win_h)
    if at is None:
        return None, "blur n/a (no ink)"
    x0, y0 = at
    clip = fitz.Rect(x0 / scale, y0 / scale, (x0 + win_w) / scale, (y0 + win_h) / scale) & page.rect
    if clip.is_empty:
        return None, "blur n/a (empty crop)"
    zoom = BLUR_CROP_DPI / 72.0
    pm = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, colorspace=fitz.csGRAY, alpha=False)
    var = _laplacian_variance(bytes(pm.samples), pm.width, pm.height)
    if var is None:
        return None, "blur n/a (crop too small)"
    return var, f"laplacian_var {var:.1f} on {pm.width}x{pm.height} px crop @{BLUR_CROP_DPI} dpi (min {BLUR_VARIANCE_MIN:.0f})"


def _pdf_page_raster_info(page) -> tuple[int, int, float | None, str]:
    """(width_px, height_px, dpi_estimate, basis) for a PDF page.

    Effective DPI is the dominant embedded raster's pixel width over the
    width it is drawn at (points / 72). Vector-only pages have no DPI.
    """
    rect = page.rect
    try:
        infos = page.get_image_info() or []
    except Exception:
        infos = []
    best = None
    best_area = 0.0
    for info in infos:
        bbox = info.get("bbox") or (0, 0, 0, 0)
        area = max(0.0, (bbox[2] - bbox[0])) * max(0.0, (bbox[3] - bbox[1]))
        if area > best_area and info.get("width") and info.get("height"):
            best, best_area = info, area
    page_area = max(1.0, rect.width * rect.height)
    if best is not None and best_area / page_area >= 0.5:
        bbox = best["bbox"]
        drawn_in = max(1e-6, (bbox[2] - bbox[0]) / 72.0)
        dpi = float(best["width"]) / drawn_in
        return (
            int(best["width"]),
            int(best["height"]),
            round(dpi, 1),
            f"embedded raster {best['width']}x{best['height']} px drawn over {drawn_in:.2f} in → {dpi:.0f} dpi",
        )
    return (
        int(round(rect.width)),
        int(round(rect.height)),
        None,
        "vector page (no dominant raster): dimensions are page points at 72 dpi; dpi not applicable",
    )


def _image_raster_info(file_bytes: bytes) -> tuple[int, int, float | None, str]:
    """(width_px, height_px, dpi_estimate, basis) for an image file.

    ``fitz.Pixmap`` reads the native pixel grid and the file's resolution
    metadata; the document page rect would be in points and hide both.
    """
    fitz = _fitz()
    pm = fitz.Pixmap(file_bytes)
    w, h = pm.width, pm.height
    xres = int(getattr(pm, "xres", 0) or 0)
    if xres > 0 and xres not in (72, 96):
        return w, h, float(xres), f"image resolution metadata {xres} dpi"
    long_side = max(w, h)
    dpi = long_side / 11.0
    return w, h, round(dpi, 1), f"no resolution metadata: {long_side} px long side / 11 in (assumed letter/A4 page) → {dpi:.0f} dpi"


def _probe_page(page, page_no: int, *, is_pdf: bool, file_bytes: bytes) -> dict[str, Any]:
    fitz = _fitz()
    width_px, height_px, dpi, dpi_basis = (
        _pdf_page_raster_info(page) if is_pdf else _image_raster_info(file_bytes)
    )
    rect = page.rect
    long_side = max(rect.width, rect.height) or 1.0
    scale = SAMPLE_LONG_SIDE_PX / long_side
    pm = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY, alpha=False)
    samples = bytes(pm.samples)
    w, h = pm.width, pm.height
    # Whole-page statistics on every 4th pixel: std and median are stable
    # under stride subsampling and the 256-pass histogram was the single
    # largest cost in the profile (9 ms/page on the full sample, 2026-09-25).
    stats_sample = samples[::4]
    hist = _histogram(stats_sample)
    _, std = _mean_std(hist, len(stats_sample))
    background = _percentile_gray(hist, len(stats_sample), 0.5)

    zoom = HIRES_DPI / 72.0
    hi = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY, alpha=False)
    hires = bytes(hi.samples)
    ratios = _region_ratios(hires, hi.width, hi.height)
    mark_ratio = ratios["mark"]
    blank = mark_ratio < BLANK_MARK_RATIO
    if blank:
        contrast_range = 0.0
        ink_level = background
        blur, blur_basis = None, "blur n/a (blank page)"
    else:
        # Ink level = the darkest quarter of the marked pixels: robust to how
        # much of the page is text, unlike the whole-page std.
        stride = hires[::4]
        ink_level = _percentile_gray_hires(stride, max(1, int(len(stride) * mark_ratio * 0.25)))
        contrast_range = float(max(0, background - ink_level))
        blur, blur_basis = _blur_crop(page, samples, w, h, scale)

    flags: list[str] = []
    basis_parts = [f"sample {w}x{h} px gray + {hi.width}x{hi.height} px @{HIRES_DPI} dpi", dpi_basis]
    if blank:
        basis_parts.append(f"blank page (marks {mark_ratio:.4f} < {BLANK_MARK_RATIO}): not flagged")
    else:
        if dpi is not None and dpi < LOW_RES_DPI:
            flags.append("low_res")
        if blur is not None and blur < BLUR_VARIANCE_MIN:
            normalised = blur * (255.0 / max(1.0, contrast_range)) ** 2
            if normalised < BLUR_VARIANCE_MIN:
                flags.append("blurry")
        if contrast_range < CONTRAST_RANGE_MIN:
            flags.append("low_contrast")
    basis_parts.append(blur_basis)
    basis_parts.append(
        f"contrast_range {contrast_range:.0f} = background {background} − ink {ink_level} (min {CONTRAST_RANGE_MIN:.0f}); contrast_std {std:.1f}"
    )
    basis_parts.append("skewed/glare/noisy not detected in V1")
    return {
        "page": page_no,
        "width_px": width_px,
        "height_px": height_px,
        "dpi_estimate": dpi,
        "blur_variance": round(blur, 2) if blur is not None else None,
        "contrast_std": round(std, 2),
        "contrast_range": contrast_range,
        "ink_ratio": round(ratios["ink"], 5),
        "mark_ratio": round(mark_ratio, 5),
        "bottom_ink_ratio": round(ratios["bottom_ink"], 5),
        "bottom_mark_ratio": round(ratios["bottom_mark"], 5),
        "body_ink_ratio": round(ratios["body_ink"], 5),
        "body_mark_ratio": round(ratios["body_mark"], 5),
        "flags": flags,
        "basis": "; ".join(basis_parts),
    }


def probe_visual_quality(file_bytes: bytes, filename: str, *, max_pages: int = 50) -> list[dict[str, Any]]:
    """Per-page visual quality of a PDF or image, ``[]`` when it cannot be rendered.

    An empty list is the honest answer for text files, corrupt bytes or an
    unknown format — never a synthetic page. Pages beyond ``max_pages`` are
    not probed (the upload limit is 50 pages, ``ASSURE_MAX_PAGES``). Extra
    keys beyond the contract (``contrast_range``, ``ink_ratio``,
    ``bottom_ink_ratio``, ``bottom_mark_ratio``, ``body_ink_ratio``) are the
    measurements :func:`assess_signature` and :func:`page_quality_score` read.
    """
    if not file_bytes or _ext(filename) in TEXT_EXTENSIONS:
        return []
    doc = _open_document(file_bytes, filename)
    if doc is None:
        return []
    pages: list[dict[str, Any]] = []
    try:
        is_pdf = bool(doc.is_pdf)
        for index in range(min(max_pages, len(doc))):
            try:
                pages.append(_probe_page(doc[index], index + 1, is_pdf=is_pdf, file_bytes=file_bytes))
            except Exception as exc:
                log.info("quality probe: page %s of %s not renderable: %s", index + 1, filename, exc)
    finally:
        doc.close()
    return pages


# --------------------------------------------------------------------------- #
# Scores
# --------------------------------------------------------------------------- #


#: Share of a full page's characters (orchestrator: 1500 chars = 1.0) at and
#: above which text density stops scaling the page score. 0.05 = 75 characters:
#: below that a "page with text" is a handful of stray glyphs. Initial value,
#: 2026-09-25.
TEXT_DENSITY_FLOOR = 0.05

#: Signature verdicts that say something about the *page* (a mark exists but is
#: hard to read) and so scale the page score. "missing" and "stamped" are
#: compliance facts about the signature field alone: measured 2026-09-25, an
#: unsigned but crisp declarations PDF scored page quality 0.50 because the
#: blank signature line halved it, and all 12 fields went to manual review.
PAGE_SIGNATURE_QUALITIES = frozenset({"faint", "incomplete", "questionable"})


def _visual_score(visual: dict[str, Any] | None) -> tuple[float | None, str]:
    """0–1 from the probe's flags and measurements; None without a probe."""
    if not visual:
        return None, ""
    score = 1.0
    parts = []
    flags = set(visual.get("flags") or [])
    blur = visual.get("blur_variance")
    std = visual.get("contrast_std")
    dpi = visual.get("dpi_estimate")
    if "blurry" in flags and blur is not None:
        factor = max(0.2, min(1.0, blur / BLUR_VARIANCE_MIN))
        score *= factor
        parts.append(f"blurry {factor:.2f}")
    rng = visual.get("contrast_range")
    if "low_contrast" in flags and rng is not None:
        factor = max(0.2, min(1.0, float(rng) / CONTRAST_RANGE_MIN))
        score *= factor
        parts.append(f"low_contrast {factor:.2f}")
    if "low_res" in flags and dpi is not None:
        factor = max(0.3, min(1.0, dpi / LOW_RES_DPI))
        score *= factor
        parts.append(f"low_res {factor:.2f}")
    if visual.get("blur_variance") is None and visual.get("contrast_std") is None:
        return None, ""
    return round(score, 3), f"visual {score:.2f}" + (f" [{', '.join(parts)}]" if parts else "")


def page_quality_score(
    *,
    visual: dict[str, Any] | None,
    ocr_confidence: float | None,
    text_density: float | None,
    parse_coverage: float | None,
    image_ratio: float | None,
    signature_quality: str | None,
) -> tuple[float | None, str]:
    """Combine the page's real signals into one 0–1 score, or ``None``.

    Product of the available factors — a missing signal contributes nothing
    (it is neither penalised nor credited). ``image_ratio`` is informative
    only: a page that is mostly image is not worse, so it appears in the
    basis but does not scale the score. Returns ``(None, "no_signal")`` when
    no factor is available at all.

    ``text_density`` measures how *much* text the page carries, not how well
    it was read, so it only counts as a floor: a page with at least
    ``TEXT_DENSITY_FLOOR`` of a full page's characters gets factor 1.0 and a
    sparser one scales down linearly (a near-blank page whose parse produced
    a few stray characters is what that catches). Measured 2026-09-25 on the
    compose stack: with density as a raw factor a crisp one-paragraph auto
    policy declarations PDF scored 0.146 and every one of its 12 fields went
    to manual review — a page-fullness figure was being read as quality.
    """
    factors: list[tuple[str, float]] = []
    vis, vis_basis = _visual_score(visual)
    if vis is not None:
        factors.append((vis_basis, vis))
    if ocr_confidence is not None:
        factors.append((f"ocr {float(ocr_confidence):.2f}", max(0.0, min(1.0, float(ocr_confidence)))))
    if text_density is not None:
        density = max(0.0, min(1.0, float(text_density)))
        density_factor = min(1.0, density / TEXT_DENSITY_FLOOR) if TEXT_DENSITY_FLOOR > 0 else 1.0
        factors.append((f"density {density:.2f}→{density_factor:.2f}", density_factor))
    if parse_coverage is not None:
        factors.append((f"coverage {float(parse_coverage):.2f}", max(0.0, min(1.0, float(parse_coverage)))))
    if signature_quality in PAGE_SIGNATURE_QUALITIES:
        factors.append((f"signature_{signature_quality} {SIGNATURE_PENALTIES[signature_quality]:.2f}", SIGNATURE_PENALTIES[signature_quality]))
    elif signature_quality and signature_quality != "unknown":
        # missing / stamped / clear: a compliance fact about the signature
        # field, not a statement about how legible the page is. It still
        # reaches the signature field through quality_weighted_confidence.
        pass
    if not factors:
        return None, "no_signal"
    score = 1.0
    for _, value in factors:
        score *= value
    basis = " × ".join(label for label, _ in factors)
    if image_ratio is not None:
        basis += f" (image_ratio {float(image_ratio):.2f}, informative)"
    return round(score, 3), basis


def quality_weighted_confidence(
    *,
    parser_confidence: float | None,
    parser_name: str,
    page_quality: float | None,
    number_quality: str | None,
    z3_violation: bool,
    signature_quality: str | None,
) -> tuple[float, str]:
    """Spec §4: ``parser × page_quality × number × z3 × signature``.

    Defaults are the spec's: parser 0.85 (jdf-cli) / 0.80 (textract) / 0.5
    otherwise, page quality 0.5 neutral. When *nothing* is known the answer is
    the conservative 0.50 with a basis that says so, never a computed-looking
    number.
    """
    name = (parser_name or "").lower()
    has_signal = any(
        v is not None and v is not False
        for v in (parser_confidence, page_quality, number_quality, signature_quality)
    ) or z3_violation
    if not has_signal and name not in PARSER_CONFIDENCE_DEFAULTS:
        return 0.50, "no_signal_available — conservative default"

    parts: list[str] = []
    if parser_confidence is not None:
        parser = max(0.0, min(1.0, float(parser_confidence)))
        parts.append(f"parser_confidence ({parser:.2f})")
    else:
        parser = PARSER_CONFIDENCE_DEFAULTS.get(name, PARSER_CONFIDENCE_FALLBACK)
        parts.append(f"parser_default[{name or 'unknown'}] ({parser:.2f})")
    if page_quality is not None:
        quality = max(0.0, min(1.0, float(page_quality)))
        parts.append(f"page_quality ({quality:.2f})")
    else:
        quality = PAGE_QUALITY_NEUTRAL
        parts.append(f"page_quality_neutral ({quality:.2f})")
    value = parser * quality
    if number_quality and number_quality in NUMBER_PENALTIES and NUMBER_PENALTIES[number_quality] != 1.0:
        penalty = NUMBER_PENALTIES[number_quality]
        value *= penalty
        parts.append(f"{number_quality}_penalty ({penalty:.2f})")
    if z3_violation:
        value *= Z3_VIOLATION_PENALTY
        parts.append(f"z3_penalty ({Z3_VIOLATION_PENALTY:.2f})")
    if signature_quality and signature_quality in SIGNATURE_PENALTIES and SIGNATURE_PENALTIES[signature_quality] != 1.0:
        penalty = SIGNATURE_PENALTIES[signature_quality]
        value *= penalty
        parts.append(f"signature_{signature_quality}_penalty ({penalty:.2f})")
    value = round(value, 2)
    return value, " × ".join(parts) + f" = {value:.2f}"


# --------------------------------------------------------------------------- #
# Signature / number assessment
# --------------------------------------------------------------------------- #


def assess_signature(
    page_text: str,
    *,
    visual: dict[str, Any] | None,
    ocr_lines: list[dict[str, Any]] | None,
    page: int | None = None,
) -> dict[str, Any]:
    """Signature presence/quality from a label in the text and bottom-region ink.

    V1 heuristic, and it says so in ``basis``: a label ("Signature", "Signed",
    "Authorized signature", "/s/") locates the expectation; the probe's ink and
    mark ratios of the bottom 20 % of the page decide the rest. The label
    line's own print is estimated from its character share of the page text
    and subtracted; what remains is the candidate mark. No remaining mark →
    ``missing``; a mark whose darkness (ink/marks) is under
    ``SIGNATURE_FAINT_FRACTION`` of the body text's → ``faint``; a mark of
    normal darkness → ``questionable`` — the heuristic cannot confirm a
    handwritten signature, so it never answers ``clear``. STAMP/SEAL/
    "electronically signed" next to the label → ``stamped``. Without a label
    the answer is ``unknown`` and nothing is asserted.
    """
    text = page_text or ""
    if not text and ocr_lines:
        text = "\n".join(str(line.get("text") or "") for line in ocr_lines if isinstance(line, dict))
    label = _SIGNATURE_LABEL.search(text)
    result: dict[str, Any] = {"present": None, "quality": "unknown", "review_required": False, "basis": "", "page": page}

    if label is None:
        result["basis"] = "no signature label in page text; presence not assessable in V1"
        return result
    label_text = label.group(0)

    window = text[max(0, label.start() - 80): label.end() + 80]
    if _STAMP_WORDS.search(window):
        result.update(
            present=True,
            quality="stamped",
            review_required=True,
            basis=f"label '{label_text}' with stamp/seal/e-signed wording nearby; stamp is not a handwritten signature",
        )
        return result

    v = visual or {}
    bottom_ink, bottom_mark = v.get("bottom_ink_ratio"), v.get("bottom_mark_ratio")
    body_ink, body_mark = v.get("body_ink_ratio"), v.get("body_mark_ratio")
    if None in (bottom_ink, bottom_mark, body_ink, body_mark):
        result.update(
            present=None,
            quality="unknown",
            review_required=True,
            basis=f"label '{label_text}' found but no visual sample to measure ink; review to confirm",
        )
        return result

    if bottom_mark < BLANK_MARK_RATIO:
        result.update(
            present=False,
            quality="missing",
            review_required=True,
            basis=f"label '{label_text}' found; signature region (bottom 20%) is blank (marks {bottom_mark:.4f})",
        )
        return result

    # Expected print of the label line itself, scaled from the body's density
    # by its character share and the region/body area ratio (0.2 / 0.8).
    line_start = text.rfind("\n", 0, label.start()) + 1
    line_end = text.find("\n", label.end())
    line_end = len(text) if line_end < 0 else line_end
    label_chars = len(text[line_start:line_end].strip())
    body_chars = max(0, len(text.strip()) - label_chars)
    area_ratio = (1.0 - SIGNATURE_REGION_FRACTION) / SIGNATURE_REGION_FRACTION
    expected_mark = (
        body_mark * (label_chars / body_chars) * area_ratio if body_chars > 0 and body_mark > 0 else 0.0
    )
    extra_mark = bottom_mark - expected_mark
    if expected_mark > 0 and extra_mark <= SIGNATURE_LABEL_TOLERANCE * expected_mark:
        result.update(
            present=False,
            quality="missing",
            review_required=True,
            basis=(
                f"label '{label_text}'; region marks {bottom_mark:.4f} ≈ the label line's own print "
                f"(expected {expected_mark:.4f}): no mark beyond the label"
            ),
        )
        return result

    expected_ink = (
        body_ink * (label_chars / body_chars) * area_ratio if body_chars > 0 and body_ink > 0 else 0.0
    )
    extra_ink = max(0.0, bottom_ink - expected_ink)
    darkness = extra_ink / extra_mark if extra_mark > 0 else 0.0
    typical = body_ink / body_mark if body_mark > 0 else darkness
    if typical > 0 and darkness < SIGNATURE_FAINT_FRACTION * typical:
        result.update(
            present=True,
            quality="faint",
            review_required=True,
            basis=(
                f"label '{label_text}'; mark darkness {darkness:.2f} < {SIGNATURE_FAINT_FRACTION:.0%} of body text "
                f"darkness {typical:.2f} (region ink {bottom_ink:.4f} / marks {bottom_mark:.4f}, label share removed)"
            ),
        )
        return result
    result.update(
        present=True,
        quality="questionable",
        review_required=True,
        basis=(
            f"label '{label_text}'; a mark beyond the label exists (marks {bottom_mark:.4f} vs label "
            f"{expected_mark:.4f}, darkness {darkness:.2f}) but the V1 heuristic cannot confirm a handwritten signature"
        ),
    )
    return result


def assess_number(
    value_text: str | None,
    *,
    ocr_confidence: float | None,
    page_quality: float | None,
    handwritten: bool | None,
) -> dict[str, Any]:
    """Number readability class and penalty (spec §6 "Unreadable Numbers")."""
    text = (value_text or "").strip()
    if not text:
        return {
            "quality": "unreadable",
            "penalty": NUMBER_PENALTIES["unreadable"],
            "review_required": True,
            "basis": "number unreadable — manual review required (no value)",
        }
    if handwritten:
        quality, basis = "handwritten", "client/parser marked the value handwritten"
    elif ocr_confidence is not None and ocr_confidence < NUMBER_FADED_OCR_MAX:
        quality, basis = "faded", f"ocr_confidence {ocr_confidence:.2f} < {NUMBER_FADED_OCR_MAX}"
    elif page_quality is not None and page_quality < NUMBER_LOW_PAGE_QUALITY_MAX:
        quality = "typewritten_low_quality"
        basis = f"page_quality {page_quality:.2f} < {NUMBER_LOW_PAGE_QUALITY_MAX}" + (
            f" with ocr_confidence {ocr_confidence:.2f}" if ocr_confidence is not None else ", no OCR confidence"
        )
    elif ocr_confidence is not None or page_quality is not None:
        quality = "printed_good"
        basis = "readable: " + ", ".join(
            p
            for p in (
                f"ocr_confidence {ocr_confidence:.2f}" if ocr_confidence is not None else "",
                f"page_quality {page_quality:.2f}" if page_quality is not None else "",
            )
            if p
        )
    else:
        quality, basis = "unknown", "no OCR confidence, page quality or handwriting signal"
    penalty = NUMBER_PENALTIES[quality]
    return {
        "quality": quality,
        "penalty": penalty,
        "review_required": penalty < 1.0,
        "basis": basis,
    }


# --------------------------------------------------------------------------- #
# Material detection
# --------------------------------------------------------------------------- #

_PROBE_PAGE_LIMIT = 3


def _pdf_has_text_layer(file_bytes: bytes) -> bool | None:
    doc = _open_document(file_bytes, "x.pdf")
    if doc is None:
        return None
    try:
        for index in range(min(_PROBE_PAGE_LIMIT, len(doc))):
            if doc[index].get_text().strip():
                return True
        return False
    except Exception:
        return None
    finally:
        doc.close()


def detect_material(file_bytes: bytes, filename: str, *, source_kind: str | None = None) -> dict[str, Any]:
    """Material type / modality / source_kind from extension, text layer and dimensions.

    Only ``pdf``, ``image``, ``photo``, ``screenshot`` and ``text_file`` are
    detected from the bytes. ``handwritten_image``, ``table`` and
    ``mixed_bundle`` are honoured only from the client's ``source_kind`` hint
    (``"mixed"`` → mixed bundle) — nothing here can tell handwriting or a table
    from an ordinary scan, so they are never guessed. A raster document image
    that is neither camera-shaped nor screen-shaped lands in the contract's
    scan bucket (``modality="scanned_pdf"``) with a basis saying it is an image.
    """
    ext = _ext(filename)
    hint = (source_kind or "").strip().lower() or None

    if hint == "mixed":
        return {
            "material_type": "mixed_bundle",
            "modality": "mixed",
            "source_kind": "mixed",
            "basis": "client source_kind=mixed hint (bundle detection is not performed in V1)",
        }
    if hint == "text" or ext in TEXT_EXTENSIONS:
        return {
            "material_type": "text_file",
            "modality": "text",
            "source_kind": "text",
            "basis": f"{'client source_kind=text hint' if hint == 'text' else 'text extension .' + ext}",
        }
    if ext == "pdf" or file_bytes[:5] == b"%PDF-":
        if hint == "scanned":
            return {"material_type": "pdf", "modality": "scanned_pdf", "source_kind": "scanned", "basis": "PDF; client source_kind=scanned hint"}
        has_text = _pdf_has_text_layer(file_bytes)
        if has_text is None:
            return {"material_type": "pdf", "modality": "digital_pdf", "source_kind": "pdf", "basis": "PDF extension; bytes could not be opened, text layer unknown"}
        if has_text:
            return {"material_type": "pdf", "modality": "digital_pdf", "source_kind": "pdf", "basis": f"PDF with a text layer in the first {_PROBE_PAGE_LIMIT} pages"}
        return {"material_type": "pdf", "modality": "scanned_pdf", "source_kind": "scanned", "basis": f"PDF with no text layer in the first {_PROBE_PAGE_LIMIT} pages"}

    if ext in IMAGE_EXTENSIONS or file_bytes[:8] == b"\x89PNG\r\n\x1a\n" or file_bytes[:3] == b"\xff\xd8\xff":
        width = height = None
        try:
            # Native pixel grid (a document page rect would be in points).
            pm = _fitz().Pixmap(file_bytes)
            width, height = int(pm.width), int(pm.height)
        except Exception:
            width = height = None
        if hint == "photo":
            return {"material_type": "photo", "modality": "phone_photo", "source_kind": "photo", "basis": "client source_kind=photo hint"}
        if hint == "scanned":
            return {"material_type": "image", "modality": "scanned_pdf", "source_kind": "scanned", "basis": "image; client source_kind=scanned hint"}
        if width and height:
            long_side, short_side = max(width, height), min(width, height)
            aspect = long_side / max(1, short_side)
            is_jpeg = ext in ("jpg", "jpeg") or file_bytes[:3] == b"\xff\xd8\xff"
            is_png = ext == "png" or file_bytes[:8] == b"\x89PNG\r\n\x1a\n"
            if is_jpeg and long_side > 1500 and 1.2 <= aspect <= 1.6:
                return {
                    "material_type": "photo",
                    "modality": "phone_photo",
                    "source_kind": "photo",
                    "basis": f"JPEG {width}x{height} (aspect {aspect:.2f}, long side > 1500 px): camera-like",
                }
            if is_png and 1.5 <= aspect <= 1.9 and 800 <= long_side <= 4000:
                return {
                    "material_type": "screenshot",
                    "modality": "screenshot",
                    "source_kind": "image",
                    "basis": f"PNG {width}x{height} (aspect {aspect:.2f}): screen-like dimensions",
                }
            return {
                "material_type": "image",
                "modality": "scanned_pdf",
                "source_kind": "image",
                "basis": f"raster image {width}x{height} (.{ext or 'unknown'}): treated as a scanned page (no text layer)",
            }
        return {
            "material_type": "image",
            "modality": "scanned_pdf",
            "source_kind": "image",
            "basis": f"image extension .{ext} but the bytes could not be opened; treated as a scan",
        }

    return {
        "material_type": "pdf",
        "modality": "digital_pdf",
        "source_kind": "pdf",
        "basis": f"unknown extension .{ext or ''}: router default (probe-and-parse as PDF)",
    }
