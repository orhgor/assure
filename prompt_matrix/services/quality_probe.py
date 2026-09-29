"""Visual quality probe and quality-weighted confidence (spec §4, §6).

Every number this module emits is measured from the bytes it was given or is
``None``; nothing is guessed. The probe renders each page to a small grayscale
sample with PyMuPDF and does the pixel arithmetic in pure Python (no Pillow,
no numpy — neither is a dependency of the image), so the sample is capped at
``SAMPLE_LONG_SIDE_PX``, ratios are counted with a single C pass
(``bytes.translate`` + ``count``) and the Laplacian runs on a small 150-dpi
crop, every other row. Measured 2026-09-25 on an M-series laptop: 50 text
pages ≈ 0.7 s, 50 raster pages ≈ 0.9 s; with the ``ink_map`` (rule erase
+ grid, 2026-09-28) 50 text pages ≈ 1.35 s.

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
  and in its bottom 20 %, the inputs :func:`assess_signature` uses when no
  label element locates the signature line.
- ``ink_map`` — a coarse grid (``INK_MAP_COLS`` columns of square cells,
  ``cell_px`` at ``HIRES_DPI``) of per-cell ink and mark fractions, measured
  on the 100-dpi render **after long straight rules are erased**
  (``RULE_MIN_PX``: ruled lines, underscores, box borders). It lets
  :func:`assess_signature` measure marks in any band of the page — the band
  right of the signature label — without re-rendering, so the same verdict
  is reachable from a saved report (replay has the visual, not the bytes).
  Added 2026-09-28 after the benchmark's signature accuracy stalled at 54 %:
  the bench pages end their signature block around 45 % of the page height,
  so the bottom-20 % band measured blank paper.

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
#: region by :func:`assess_signature` when no label element locates the line.
SIGNATURE_REGION_FRACTION = 0.20

#: Columns of the per-page ``ink_map`` grid; cells are square, so a US-Letter
#: page at 100 dpi is 48 × 62 cells of 18 px (4.6 mm) — about one 10-pt text
#: line per cell row. 48 keeps the two hex-encoded maps under 13 KB per page
#: in the saved report (measured 2026-09-28: 6.0 KB each).
INK_MAP_COLS = 48

#: A run of marked pixels at least this long (px at ``HIRES_DPI``; 40 px =
#: 10 mm) in one pixel row or column is a straight rule — a ruled signature
#: line, a row of underscores, a form box border — and is erased before the
#: ``ink_map`` is built. Glyphs do not make 10 mm straight runs; a cursive
#: stroke that is straight for 10 mm in one pixel row would be erased too,
#: which the basis says.
RULE_MIN_PX = 40

#: Mark fraction of the label band under which the band is blank paper. The
#: band is small (≈ 60 cells / 20 k px on the bench pages), so this is ≈ 80
#: marked pixels: less than two 10-pt letters (proportional-width estimate of
#: where the label's print ends can leak a letter into the band), far under a
#: stroke (the bench stroke measured 0.05–0.09, 2026-09-28).
SIGNATURE_BAND_BLANK_MARK = 0.004

#: The label band's vertical extent in line heights: this far above the label
#: line's top (signatures ride above the baseline; more would reach the
#: previous line's descenders) and this far below its bottom (the line under
#: the label, where a signature written low lands).
SIGNATURE_BAND_ABOVE_LINES = 0.35
SIGNATURE_BAND_BELOW_LINES = 1.0

#: Typical 10-pt text at 100 dpi is one 18-px cell tall; a bbox this short
#: (relative page units, ≈ 6 pt on Letter) cannot be a whole text line, so a
#: multi-line element with a shorter per-line share is treated as carrying
#: jdf-cli's one-line fallback height rather than its true height.
MIN_LINE_HEIGHT_REL = 0.008

#: A band narrower than this share of the page width (≈ 2.6 cm on Letter, 5
#: ink-map cells) is no room to sign in; the band moves under the label.
MIN_BAND_WIDTH_REL = 0.12

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

#: Vocabulary since 2026-09-27 (customer: "questionable" told the reviewer
#: nothing): present_clear / present_ambiguous / missing / stamp / printed_name /
#: unreadable; the older words stay so reports saved before the change still score.
SIGNATURE_PENALTIES: dict[str, float] = {
    "clear": 1.0,
    "present_clear": 1.0,
    "faint": 0.8,
    "incomplete": 0.7,
    "stamped": 0.7,
    "stamp": 0.7,
    "questionable": 0.6,
    "present_ambiguous": 0.6,
    "printed_name": 0.6,
    "missing": 0.5,
    "unreadable": 0.4,
    "unknown": 1.0,
}

#: Z3 penalty for a field whose constraint was violated (spec §4 example 0.9).
Z3_VIOLATION_PENALTY = 0.9

#: Parser-confidence defaults when the parser reported none (spec §4).
#: ``llm`` (2026-09-29, PARSER_BACKEND=openrouter): a frontier multimodal model
#: transcribing a rendered page; it reports no measured confidence, so this
#: policy default stands in — the text-layer figure, stated in the basis as
#: ``parser_default[llm]`` so a reader knows it is a default, not a measurement.
PARSER_CONFIDENCE_DEFAULTS: dict[str, float] = {"jdf-cli": 0.85, "jdf": 0.85, "textract": 0.80, "jdf-cli+tesseract": 0.80, "llm": 0.85}
PARSER_CONFIDENCE_FALLBACK = 0.5
PAGE_QUALITY_NEUTRAL = 0.5

IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "tif", "tiff", "bmp"})
TEXT_EXTENSIONS = frozenset({"txt", "md", "json", "csv", "rst", "yaml", "yml"})

#: Flags this module can emit. ``skewed``/``glare``/``noisy`` are in the
#: contract but NOT detected in V1 (see module docstring).
DETECTED_FLAGS = ("low_res", "blurry", "low_contrast")
UNDETECTED_FLAGS = ("skewed", "glare", "noisy")

#: Word-bounded since 2026-09-28: without ``\b`` the bench declarations page
#: matched "signed" inside "Countersigned this 20th day…" two lines above the
#: real "Authorized Signature:" line, and the band was measured on the wrong
#: line. :func:`_pick_label` prefers a "signature" label over a bare "signed".
_SIGNATURE_LABEL = re.compile(
    r"\b(authori[sz]ed\s+signature|signature|signed\s+by|signed)\b|/s/", re.IGNORECASE
)
_LABEL_RANK = ("signature", "signed by", "/s/", "signed")


def _pick_label(text: str) -> "re.Match[str] | None":
    """The signature label to measure against: the first match of the
    highest-ranked kind (a "signature" label beats "signed by", which beats
    "/s/" and a bare "signed"), so a page that says both "Countersigned …" and
    "Authorized Signature:" is judged at the signature line."""
    matches = list(_SIGNATURE_LABEL.finditer(text))
    if not matches:
        return None

    def rank(m: "re.Match[str]") -> int:
        word = " ".join(m.group(0).lower().split())
        for i, kind in enumerate(_LABEL_RANK):
            if kind in word:
                return i
        return len(_LABEL_RANK)

    return min(matches, key=lambda m: (rank(m), m.start()))
_STAMP_WORDS = re.compile(r"\b(stamp(ed)?|seal)\b", re.IGNORECASE)
#: An electronic signature is a signature (``present_clear``), not a stamp —
#: the two were one verdict ("stamped") until 2026-09-27.
_ESIGN_WORDS = re.compile(r"\b(electronically\s+signed|e-?signed|docusign|digitally\s+signed)\b|/s/", re.IGNORECASE)
_TYPED_NAME_AFTER_LABEL = re.compile(r"^[\s:\-–—_]*([A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+){1,3})\s*$")


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


_RULE_RUN = re.compile(rb"\x01{%d,}" % RULE_MIN_PX)
#: One translate table for the ink map: ink → 1, faint mark → 2, paper → 0.
_INK_MAP_TABLE = bytes(1 if v <= INK_THRESHOLD else (2 if v <= MARK_THRESHOLD else 0) for v in range(256))


def _erase_rules(buf: bytearray, width: int, height: int) -> tuple[int, int]:
    """Blank (to white) every horizontal and vertical run of ≥ ``RULE_MIN_PX``
    marked pixels in place; returns ``(horizontal runs, vertical runs)``.

    Rows and columns are scanned as byte strings (``translate`` + one regex
    per row/column, C speed): 8.9 ms for a 850 × 1100 px page, 2026-09-28.
    Column slices ``buf[x::width]`` and the strided assignment back are the
    reason the raster is a ``bytearray``.
    """
    table = _MASK_TABLES.get(MARK_THRESHOLD)
    if table is None:
        table = bytes(1 if v <= MARK_THRESHOLD else 0 for v in range(256))
        _MASK_TABLES[MARK_THRESHOLD] = table
    horizontal = vertical = 0
    for y in range(height):
        base = y * width
        row = bytes(buf[base:base + width]).translate(table)
        for m in _RULE_RUN.finditer(row):
            buf[base + m.start():base + m.end()] = b"\xff" * (m.end() - m.start())
            horizontal += 1
    for x in range(width):
        col = bytes(buf[x::width]).translate(table)
        for m in _RULE_RUN.finditer(col):
            buf[x + width * m.start():x + width * m.end():width] = b"\xff" * (m.end() - m.start())
            vertical += 1
    return horizontal, vertical


def _ink_map(buf: bytes | bytearray, width: int, height: int, *, rules: tuple[int, int]) -> dict[str, Any]:
    """Per-cell ink / mark fractions of a rule-erased raster, hex-encoded.

    ``ink`` and ``mark`` are strings of two hex digits per cell (row-major,
    ``rows × cols``), each ``round(fraction × 255)``; :func:`ink_map_region`
    decodes them. 11 ms for 48 × 62 cells at 100 dpi (2026-09-28).
    """
    cols = INK_MAP_COLS
    cell = max(1, -(-width // cols))
    rows = max(1, -(-height // cell))
    ink = [0] * (cols * rows)
    mark = [0] * (cols * rows)
    for y in range(height):
        row = bytes(buf[y * width:(y + 1) * width]).translate(_INK_MAP_TABLE)
        base = (y // cell) * cols
        for c in range(cols):
            seg = row[c * cell:(c + 1) * cell]
            a = seg.count(b"\x01")
            ink[base + c] += a
            mark[base + c] += a + seg.count(b"\x02")
    area = float(cell * cell)
    return {
        "cols": cols,
        "rows": rows,
        "cell_px": cell,
        "dpi": HIRES_DPI,
        "ink": "".join(f"{min(255, round(255 * v / area)):02x}" for v in ink),
        "mark": "".join(f"{min(255, round(255 * v / area)):02x}" for v in mark),
        "rules_erased": {"horizontal": rules[0], "vertical": rules[1], "min_px": RULE_MIN_PX},
    }


def ink_map_region(visual: dict[str, Any] | None, bbox: list[float] | tuple[float, ...]) -> dict[str, Any] | None:
    """Mean ink and mark fractions of the ``ink_map`` cells whose centre lies in
    the relative ``bbox`` ``[x0, y0, x1, y1]``; None when the visual has no
    map or the bbox covers no cell centre. ``cells`` says how many cells were
    read so a caller can state the measurement's grain.
    """
    m = (visual or {}).get("ink_map") if isinstance(visual, dict) else None
    if not isinstance(m, dict):
        return None
    try:
        cols, rows = int(m["cols"]), int(m["rows"])
        ink_hex, mark_hex = str(m["ink"]), str(m["mark"])
        x0, y0, x1, y1 = (float(v) for v in bbox)
    except (KeyError, TypeError, ValueError):
        return None
    if len(ink_hex) < 2 * cols * rows or len(mark_hex) < 2 * cols * rows:
        return None
    c0, c1 = max(0, int(x0 * cols)), min(cols, int(x1 * cols) + 1)
    r0, r1 = max(0, int(y0 * rows)), min(rows, int(y1 * rows) + 1)
    ink_sum = mark_sum = 0
    n = 0
    for r in range(r0, r1):
        cy = (r + 0.5) / rows
        if not (y0 <= cy <= y1):
            continue
        for c in range(c0, c1):
            cx = (c + 0.5) / cols
            if not (x0 <= cx <= x1):
                continue
            i = 2 * (r * cols + c)
            ink_sum += int(ink_hex[i:i + 2], 16)
            mark_sum += int(mark_hex[i:i + 2], 16)
            n += 1
    if n == 0:
        return None
    return {"ink": ink_sum / (255.0 * n), "mark": mark_sum / (255.0 * n), "cells": n}


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
    # Ink map on a copy with the straight rules erased; the page ratios above
    # keep counting the rules (a ruled form is not blank paper).
    erased = bytearray(hires)
    rules = _erase_rules(erased, hi.width, hi.height) if not blank else (0, 0)
    ink_map = _ink_map(erased, hi.width, hi.height, rules=rules)
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
        "ink_map": ink_map,
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
PAGE_SIGNATURE_QUALITIES = frozenset({"faint", "incomplete", "questionable", "present_ambiguous", "unreadable"})


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


def grounding_floor(*, grounding_success: float | None, ocr_confidence: float | None) -> tuple[float | None, str | None]:
    """The readability the extraction itself proved: ``grounding_success`` (the
    share of located reads on the page that had a usable value shape,
    ``raw_candidates.grounding_success_by_page``) times the page's OCR
    word-confidence mean. Both are required: without an OCR figure there is
    no measurement that the *reader* saw the page (a text-layer page with a
    poor visual probe keeps the probe's score — tests/test_v1_orchestrator
    ``test_evidence_states_separate_absent_from_unreadable``). Returns
    ``(floor, basis)`` or ``(None, None)`` when either signal is missing.

    Why (plan V4 Part 4, measured 2026-09-28 on the customer's site-report
    photo): the visual probe's low-resolution and contrast factors multiplied
    the page to 0.07 while tesseract read it at 0.80 and the label pass and the
    model quoted its lines verbatim — a page that was read cannot be
    unreadable. The floor never lifts a page nothing was read from (a debris
    scan keeps its low score: its located reads are suspects, so the share is
    0.0 and the floor is 0.0)."""
    if grounding_success is None or ocr_confidence is None:
        return None, None
    share = max(0.0, min(1.0, float(grounding_success)))
    ocr = max(0.0, min(1.0, float(ocr_confidence)))
    floor = round(share * ocr, 3)
    return floor, f"grounded reads {share:.2f} × ocr {ocr:.2f} = floor {floor:.2f}"


def page_quality_score(
    *,
    visual: dict[str, Any] | None,
    ocr_confidence: float | None,
    text_density: float | None,
    parse_coverage: float | None,
    image_ratio: float | None,
    signature_quality: str | None,
    grounding_success: float | None = None,
    reader_default: float | None = None,
) -> tuple[float | None, str]:
    """Combine the page's real signals into one 0–1 score, or ``None``.

    ``reader_default`` (2026-09-29, PARSER_BACKEND=openrouter): a hosted model
    that read the page reports no confidence figure; the caller passes the
    parser's stated default (``PARSER_CONFIDENCE_DEFAULTS["llm"]``) and the
    page is scored ``reader_default × coverage`` like an OCR page — the visual
    probe is not readability (a photo the model transcribed in full scored
    0.14 on blur and dpi). The basis names it a default.

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

    ``grounding_success`` (plan V4 Part 4, 2026-09-28) is the extraction's own
    readability evidence, known only after the fields were read; when given,
    the score is at least :func:`grounding_floor` and the basis says so. The
    probe factors are otherwise unchanged.
    """
    factors: list[tuple[str, float]] = []
    reader_label = "ocr"
    if ocr_confidence is None and reader_default is not None:
        ocr_confidence, reader_label = float(reader_default), "reader_default[llm]"
    floor, floor_basis = grounding_floor(grounding_success=grounding_success, ocr_confidence=ocr_confidence)
    if ocr_confidence is not None:
        # An OCR-read page (Textract since 2026-09-29; tesseract before) is
        # scored by what the reader reported: its line-confidence mean times
        # the share of the page the parse covered. User decision 2026-09-29:
        # the visual probe (blur, contrast, dpi) and the signature verdict are
        # not readability — a crisp CMS-1500 read at 0.96 scored 0.58 because
        # its signature box was "ambiguous". They stay in the basis as
        # informative notes; the signature still reaches the signature field
        # through quality_weighted_confidence.
        ocr = max(0.0, min(1.0, float(ocr_confidence)))
        factors.append((f"{reader_label} {ocr:.2f}", ocr))
        if parse_coverage is not None:
            factors.append((f"coverage {float(parse_coverage):.2f}", max(0.0, min(1.0, float(parse_coverage)))))
        score = 1.0
        for _, value in factors:
            score *= value
        basis = " × ".join(label for label, _ in factors)
        notes = []
        vis, vis_basis = _visual_score(visual)
        if vis is not None:
            notes.append(f"visual {vis_basis}")
        if signature_quality in PAGE_SIGNATURE_QUALITIES:
            notes.append(f"signature_{signature_quality}")
        if image_ratio is not None:
            notes.append(f"image_ratio {float(image_ratio):.2f}")
        if notes:
            basis += " (" + ", ".join(notes) + "; informative)"
        score = round(score, 3)
        if floor is not None and floor > score:
            basis += f"; lifted to {floor_basis}"
            score = floor
        elif floor is not None:
            basis += f"; {floor_basis} (below the ocr score)"
        return score, basis
    vis, vis_basis = _visual_score(visual)
    if vis is not None:
        factors.append((vis_basis, vis))
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
        if floor is not None:
            return floor, f"no probe signal; {floor_basis}"
        return None, "no_signal"
    score = 1.0
    for _, value in factors:
        score *= value
    basis = " × ".join(label for label, _ in factors)
    if image_ratio is not None:
        basis += f" (image_ratio {float(image_ratio):.2f}, informative)"
    score = round(score, 3)
    if floor is not None and floor > score:
        basis += f"; lifted to {floor_basis}"
        score = floor
    elif floor is not None:
        basis += f"; {floor_basis} (below the probe score)"
    return score, basis


def quality_weighted_confidence(
    *,
    parser_confidence: float | None,
    parser_name: str,
    page_quality: float | None,
    number_quality: str | None,
    z3_violation: bool,
    signature_quality: str | None,
    quality_label: str = "page_quality",
) -> tuple[float, str]:
    """Spec §4: ``parser × page_quality × number × z3 × signature``.

    Defaults are the spec's: parser 0.85 (jdf-cli) / 0.80 (textract) / 0.5
    otherwise, page quality 0.5 neutral. When *nothing* is known the answer is
    the conservative 0.50 with a basis that says so, never a computed-looking
    number. ``quality_label`` names the quality factor in the basis —
    ``page_quality`` for the page-global score, ``local_ocr`` when the caller
    passed the OCR confidence of the line the value sits on (field-level
    confidence, 2026-09-26; the formula is the same, the factor is nearer).
    """
    name = (parser_name or "").lower()
    if name not in PARSER_CONFIDENCE_DEFAULTS and ":" in name and name.split(":", 1)[0] in PARSER_CONFIDENCE_DEFAULTS:
        name = name.split(":", 1)[0]  # ``llm:anthropic/claude-opus-5.5`` → the ``llm`` default
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
        parts.append(f"{quality_label} ({quality:.2f})")
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


#: Punctuation between a label and what follows it. Underscores and dots
#: are *not* separators: they are the ruled space the signature goes on.
_SIGNATURE_SEPARATOR = re.compile(r"^[\s:\-–—]*")
#: A "print token" in the text right of the label on its line: two or more
#: word characters. OCR reads an ink stroke as one or two stray glyphs
#: ("—l" for the bench stroke, 2026-09-28); those are not print.
_PRINT_TOKEN = re.compile(r"[A-Za-z0-9]{2,}")
#: A ruled gap on the line: three or more underscores/dots, or a run of
#: spaces wide enough to sign in.
_RULE_GAP = re.compile(r"[_.]{3,}|\s{4,}")


def _label_band(
    text: str, label: "re.Match[str]", typed: "re.Match[str] | None", layout: list[dict] | None
) -> tuple[list[float], dict[str, Any]] | None:
    """:func:`_band_for_match` for the chosen label, then for every other
    occurrence of the same label wording on the page. A scan's tree carries
    the OCR text twice — on the image node (no bbox, no elements) and on the
    OCR paragraph (with ``ocr_block`` entries) — and the first occurrence is
    the one without geometry (measured 2026-09-28 on the bench stroke scan)."""
    if not layout:
        return None
    band = _band_for_match(text, label, typed, layout)
    if band is not None:
        return band
    wording = label.group(0).lower()
    for other in _SIGNATURE_LABEL.finditer(text):
        if other.start() == label.start() or other.group(0).lower() != wording:
            continue
        band = _band_for_match(text, other, typed, layout)
        if band is not None:
            return band
    return None


def _band_for_match(
    text: str, label: "re.Match[str]", typed: "re.Match[str] | None", layout: list[dict]
) -> tuple[list[float], dict[str, Any]] | None:
    """The band right of (or under) the signature label, in relative page
    coordinates, from the layout segment (and its finest ``elements`` entry)
    that carries the label; None when the layout has no bbox for it.

    Where the label's print ends is estimated proportionally by character
    offset inside the element's line (jdf-cli gives one ``width`` per
    element, no per-glyph metrics), so the estimate can be off by a glyph or
    two — the basis says so. The band starts after the label and its
    separator, or after a typed name when one follows the label; it stops at
    the next real print token after a ruled gap (a "Date:" caption), at the
    left edge of another segment on the same line, or at the page's right
    margin. Vertically it spans ``SIGNATURE_BAND_ABOVE_LINES`` above the
    label line to ``SIGNATURE_BAND_BELOW_LINES`` below it, clipped at the top
    of the next segment underneath. When the caption simply continues after
    the label ("SIGNATURE OF PHYSICIAN OR SUPPLIER …", a form box) the space
    to sign is *under* the caption, so the band is the line below it, from
    the label's left edge.
    """
    seg = next(
        (s for s in layout if isinstance(s, dict) and isinstance(s.get("start"), int) and isinstance(s.get("end"), int)
         and s["start"] <= label.start() <= s["end"]),
        None,
    )
    if seg is None:
        return None
    rel = label.start() - int(seg["start"])
    seg_text = str(seg.get("text") or "")
    box = seg.get("bbox")
    el_text, el_offset, grain = seg_text, 0, "segment"
    best: tuple[float, dict] | None = None
    for el in seg.get("elements") or []:
        if not isinstance(el, dict):
            continue
        s, e, bb = el.get("start_char"), el.get("end_char"), el.get("bbox")
        if not (isinstance(s, int) and isinstance(e, int) and s <= rel < max(e, s + 1)):
            continue
        if not (isinstance(bb, (list, tuple)) and len(bb) == 4 and all(isinstance(v, (int, float)) for v in bb)):
            continue
        area = max(0.0, (bb[2] - bb[0]) * (bb[3] - bb[1]))
        if best is None or area < best[0]:
            best = (area, el)
    if best is not None:
        el = best[1]
        box = list(el["bbox"])
        el_offset = int(el["start_char"])
        el_text = seg_text[el_offset:int(el["end_char"])]
        grain = str(el.get("kind") or "element")
    if not (isinstance(box, (list, tuple)) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
        return None
    x0, y0, x1, y1 = (float(v) for v in box)
    if x1 <= x0 or y1 <= y0:
        return None
    rel_el = rel - el_offset
    lines = el_text.split("\n")
    line_index, consumed = 0, 0
    for i, line in enumerate(lines):
        if consumed + len(line) >= rel_el:
            line_index = i
            break
        consumed += len(line) + 1
    line_text = lines[line_index] if line_index < len(lines) else el_text
    n_lines = max(1, len(lines))
    per_line = (y1 - y0) / n_lines
    if n_lines > 1 and per_line < MIN_LINE_HEIGHT_REL:
        # jdf-cli emits no height; field_extractor's fallback is one line tall.
        line_h, line_y0 = (y1 - y0), y0 + line_index * (y1 - y0)
    else:
        line_h, line_y0 = per_line, y0 + line_index * per_line
    # Character offsets inside the line: label end (+ separator), typed name end, next print token.
    in_line = rel_el - consumed
    label_end = in_line + (label.end() - label.start())
    tail = line_text[label_end:]
    sep = _SIGNATURE_SEPARATOR.match(tail)
    start_chars = label_end + (sep.end() if sep else 0)
    stop_chars = len(line_text)
    start_kind = "after the label"
    if typed is not None:
        start_chars, start_kind = label_end + typed.end(), "after the typed name"
    else:
        after_sep = tail[(sep.end() if sep else 0):]
        tok = _PRINT_TOKEN.search(after_sep)
        if tok is not None:
            if _RULE_GAP.search(after_sep[:tok.start()]):
                # "Signature: ______ Date: ____": the ruled gap before the
                # next caption is the signature space.
                stop_chars = start_chars + tok.start()
            else:
                # "SIGNATURE OF PHYSICIAN OR SUPPLIER …": the caption goes
                # on; the space to sign is right of the whole printed line.
                start_chars, start_kind = len(line_text), "after the printed caption"
    width = x1 - x0
    n_chars = max(1, len(line_text))
    if start_kind == "after the label" and (1.0 - 0.03) - (x0 + width * min(1.0, start_chars / n_chars)) < MIN_BAND_WIDTH_REL:
        # The label sits at the right edge ("…            Signature" as a
        # column caption): there is no room to sign beside it, so the
        # space is under it, as with a continuing caption.
        start_kind = "after the printed caption"
    if start_kind == "after the printed caption":
        band_x0 = x0 + width * min(1.0, in_line / n_chars)
        band_x1 = 1.0 - 0.03
        band_y0 = line_y0 + line_h
        band_y1 = min(1.0, line_y0 + line_h + (SIGNATURE_BAND_BELOW_LINES + SIGNATURE_BAND_ABOVE_LINES) * line_h)
    else:
        band_x0 = x0 + width * min(1.0, start_chars / n_chars)
        band_x1 = 1.0 - 0.03 if stop_chars >= len(line_text) else x0 + width * (stop_chars / n_chars)
        band_y0 = max(0.0, line_y0 - SIGNATURE_BAND_ABOVE_LINES * line_h)
        band_y1 = min(1.0, line_y0 + line_h + SIGNATURE_BAND_BELOW_LINES * line_h)
    clipped: list[str] = []
    for other in layout:
        if other is seg or not isinstance(other, dict):
            continue
        ob = other.get("bbox")
        if not (isinstance(ob, (list, tuple)) and len(ob) == 4 and all(isinstance(v, (int, float)) for v in ob)):
            continue
        ox0, oy0, ox1, oy1 = (float(v) for v in ob)
        if ox1 <= band_x0 or ox0 >= band_x1:
            continue
        if oy0 < line_y0 + line_h and oy1 > line_y0 and ox0 > band_x0 and start_kind != "after the printed caption":
            band_x1 = min(band_x1, ox0)
            clipped.append("a segment on the same line")
        elif line_y0 + line_h <= oy0 < band_y1 and oy0 > band_y0:
            band_y1 = min(band_y1, oy0)
            clipped.append("the segment below")
    if band_x1 <= band_x0 or band_y1 <= band_y0:
        return None
    info = {
        "grain": grain,
        "line_height": round(line_h, 4),
        "start": start_kind,
        "stop": ("at a print token on the line" if stop_chars < len(line_text) else "at the page margin"),
        "under_caption": start_kind == "after the printed caption",
        "clipped": sorted(set(clipped)),
    }
    return [round(band_x0, 4), round(band_y0, 4), round(band_x1, 4), round(band_y1, 4)], info


def assess_signature(
    page_text: str,
    *,
    visual: dict[str, Any] | None,
    ocr_lines: list[dict[str, Any]] | None,
    page: int | None = None,
    layout: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Signature presence/quality from a label in the text and the ink around it.

    V1 heuristic, and it says so in ``basis``: a label ("Signature", "Signed",
    "Authorized signature", "/s/") locates the expectation. Since 2026-09-28
    the ink is measured where the label *is*: ``layout`` (the page's
    ``field_extractor.page_layout`` segments) gives the label's element bbox,
    :func:`_label_band` the band right of and one line under it, and the
    probe's rule-erased ``ink_map`` the marks in that band — ruled lines and
    underscores are not marks, print left of the band is not in it. The
    benchmark's signature pages end their signature block near 45 % of the
    page height, where the old bottom-20 % band saw blank paper (accuracy
    54 %, 7/13). Without a layout bbox for the label, or with a visual from
    before the ink map existed, the bottom 20 % of the page is the region
    (the label line's own print estimated from its character share and
    subtracted) — ``region.basis`` says which of the two was used.

    Verdicts: no mark in the region → ``missing`` (or ``printed_name`` when
    a typed name follows the label on its line); a mark → ``present_ambiguous``
    (the basis says whether it is faint) — the heuristic cannot confirm a
    handwritten signature, so it never answers ``present_clear`` from ink
    alone. STAMP/SEAL next to the label → ``stamp``; an electronic-signature
    marker (/s/, "electronically signed", DocuSign) → ``present_clear``. No
    visual sample → ``unreadable``. Without a label the answer is ``unknown``
    and nothing is asserted. Vocabulary of 2026-09-27;
    ``field_extractor.SIGNATURE_NEXT_CHECK`` adds the reviewer's next step.
    """
    text = page_text or ""
    if not text and ocr_lines:
        text = "\n".join(str(line.get("text") or "") for line in ocr_lines if isinstance(line, dict))
    label = _pick_label(text)
    result: dict[str, Any] = {"present": None, "quality": "unknown", "review_required": False, "basis": "", "page": page}

    if label is None:
        result["basis"] = "no signature label in page text; presence not assessable in V1"
        return result
    label_text = label.group(0)

    window = text[max(0, label.start() - 80): label.end() + 80]
    if _ESIGN_WORDS.search(window):
        result.update(
            present=True,
            quality="present_clear",
            review_required=False,
            basis=f"label '{label_text}' with an explicit electronic-signature marker nearby",
        )
        return result
    if _STAMP_WORDS.search(window):
        result.update(
            present=True,
            quality="stamp",
            review_required=True,
            basis=f"label '{label_text}' with stamp/seal wording nearby; a stamp is not a handwritten signature",
        )
        return result
    line_end_ = text.find("\n", label.end())
    same_line = text[label.end(): len(text) if line_end_ < 0 else line_end_]
    typed = _TYPED_NAME_AFTER_LABEL.match(same_line)

    v = visual or {}
    bottom_ink, bottom_mark = v.get("bottom_ink_ratio"), v.get("bottom_mark_ratio")
    body_ink, body_mark = v.get("body_ink_ratio"), v.get("body_mark_ratio")
    if None in (bottom_ink, bottom_mark, body_ink, body_mark):
        if typed:
            result.update(
                present=True,
                quality="printed_name",
                review_required=True,
                basis=f"label '{label_text}' followed by a typed name ('{typed.group(1)}') on the same line; no visual sample to see ink",
            )
            return result
        result.update(
            present=None,
            quality="unreadable",
            review_required=True,
            basis=f"label '{label_text}' found but no visual sample to measure ink; view the original to confirm",
        )
        return result

    band = _label_band(text, label, typed, layout)
    measured = ink_map_region(v, band[0]) if band is not None else None
    if band is not None and measured is not None:
        return _verdict_from_band(result, label_text=label_text, typed=typed, band=band, measured=measured, body_ink=body_ink, body_mark=body_mark, page=page)

    result["region"] = {
        "page": page,
        "bbox": [0.0, round(1.0 - SIGNATURE_REGION_FRACTION, 4), 1.0, 1.0],
        "basis": "bottom band",
        "reason": ("no ink map on the visual sample" if band is not None else "no layout bbox for the label element"),
    }
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
        if typed:
            result.update(
                present=True,
                quality="printed_name",
                review_required=True,
                basis=(
                    f"label '{label_text}' followed by a typed name ('{typed.group(1)}'); region marks {bottom_mark:.4f} ≈ "
                    f"the printed line's own (expected {expected_mark:.4f}): print, no ink beyond it"
                ),
            )
            return result
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
            quality="present_ambiguous",
            review_required=True,
            basis=(
                f"label '{label_text}'; faint mark: darkness {darkness:.2f} < {SIGNATURE_FAINT_FRACTION:.0%} of body text "
                f"darkness {typical:.2f} (region ink {bottom_ink:.4f} / marks {bottom_mark:.4f}, label share removed)"
            ),
        )
        return result
    result.update(
        present=True,
        quality="present_ambiguous",
        review_required=True,
        basis=(
            f"label '{label_text}'; a mark beyond the label exists (marks {bottom_mark:.4f} vs label "
            f"{expected_mark:.4f}, darkness {darkness:.2f}) but the V1 heuristic cannot confirm a handwritten signature"
        ),
    )
    return result


def _verdict_from_band(
    result: dict[str, Any], *, label_text: str, typed: "re.Match[str] | None", band: tuple[list[float], dict[str, Any]],
    measured: dict[str, Any], body_ink: float, body_mark: float, page: int | None,
) -> dict[str, Any]:
    """The verdict when the label band could be located and measured."""
    bbox, info = band
    mark, ink, cells = float(measured["mark"]), float(measured["ink"]), int(measured["cells"])
    result["region"] = {
        "page": page,
        "bbox": bbox,
        "basis": "label element",
        "grain": info["grain"],
        "cells": cells,
        "mark_ratio": round(mark, 5),
        "ink_ratio": round(ink, 5),
        "band": f"{info['start']}, {info['stop']}, {SIGNATURE_BAND_ABOVE_LINES:g} line above to {SIGNATURE_BAND_BELOW_LINES:g} line below"
                + (f"; clipped at {', '.join(info['clipped'])}" if info["clipped"] else ""),
    }
    where = f"band right of the label ({cells} cells of the rule-erased ink map, label print end estimated by character share)"
    if mark < SIGNATURE_BAND_BLANK_MARK:
        if typed:
            result.update(
                present=True,
                quality="printed_name",
                review_required=True,
                basis=(
                    f"label '{label_text}' followed by a typed name ('{typed.group(1)}'); {where} is blank "
                    f"(marks {mark:.4f} < {SIGNATURE_BAND_BLANK_MARK}): print, no ink beyond it"
                ),
            )
            return result
        result.update(
            present=False,
            quality="missing",
            review_required=True,
            basis=f"label '{label_text}'; {where} is blank (marks {mark:.4f} < {SIGNATURE_BAND_BLANK_MARK}): no mark beyond the label",
        )
        return result
    darkness = ink / mark if mark > 0 else 0.0
    typical = body_ink / body_mark if body_mark > 0 else darkness
    if typical > 0 and darkness < SIGNATURE_FAINT_FRACTION * typical:
        result.update(
            present=True,
            quality="present_ambiguous",
            review_required=True,
            basis=(
                f"label '{label_text}'; faint mark in the {where}: darkness {darkness:.2f} < {SIGNATURE_FAINT_FRACTION:.0%} of body text "
                f"darkness {typical:.2f} (band ink {ink:.4f} / marks {mark:.4f})"
            ),
        )
        return result
    result.update(
        present=True,
        quality="present_ambiguous",
        review_required=True,
        basis=(
            f"label '{label_text}'; a mark exists in the {where} (marks {mark:.4f}, darkness {darkness:.2f}) "
            f"but the V1 heuristic cannot confirm a handwritten signature"
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
