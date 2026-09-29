"""Quality probe: every number is measured or None; flags come from fixtures, not guesses.

Fixtures are generated with PyMuPDF so the calibration constants in
``services/quality_probe`` are tested against exactly the material they were
measured on (2026-09-25): crisp 11-pt text, the same page as a low-dpi
raster, a soft (down-then-up-scaled) raster, gray-on-gray text, a blank page,
PNG/JPEG exports and signature pages.
"""

from __future__ import annotations

import time

import fitz
import pytest

from prompt_matrix.services import quality_probe as qp
from prompt_matrix.services.quality_probe import (
    NUMBER_PENALTIES,
    SIGNATURE_PENALTIES,
    assess_number,
    assess_signature,
    detect_material,
    page_quality_score,
    probe_visual_quality,
    quality_weighted_confidence,
)

TEXT = (
    "Policy number 4471-0021. Insured: Jane Example. Premium USD 1,240.00. "
    "Coverage: collision and comprehensive. Effective 2026-01-01."
)


def crisp_pdf(pages: int = 1, lines: int = 30) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        y = 72
        for _ in range(lines):
            page.insert_text((54, y), TEXT[:90], fontsize=11)
            y += 16
    data = doc.tobytes()
    doc.close()
    return data


def rerender_pdf(pdf: bytes, dpi: int) -> bytes:
    """Each page replaced by its own raster at ``dpi`` — a scan of that resolution."""
    src = fitz.open(stream=pdf, filetype="pdf")
    out = fitz.open()
    for page in src:
        pm = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=pm.tobytes("png"))
    data = out.tobytes()
    out.close()
    src.close()
    return data


def soft_pdf(pdf: bytes, low_dpi: int = 25, out_dpi: int = 200) -> bytes:
    """A raster at ``out_dpi`` that was scaled down to ``low_dpi`` and smoothly
    back up: soft gradients at an adequate nominal resolution, i.e. an
    out-of-focus photo, not a low-resolution scan."""
    src = fitz.open(stream=pdf, filetype="pdf")
    out = fitz.open()
    for page in src:
        pm = page.get_pixmap(dpi=out_dpi, colorspace=fitz.csGRAY)
        small = fitz.Pixmap(pm, int(pm.width * low_dpi / out_dpi), int(pm.height * low_dpi / out_dpi), None)
        big = fitz.Pixmap(small, pm.width, pm.height, None)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=big.tobytes("png"))
    data = out.tobytes()
    out.close()
    src.close()
    return data


def low_contrast_pdf(bg: float = 0.85, fg: float = 0.55) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(page.rect, color=None, fill=(bg, bg, bg))
    y = 72
    for _ in range(30):
        page.insert_text((54, y), TEXT[:90], fontsize=11, color=(fg, fg, fg))
        y += 16
    data = doc.tobytes()
    doc.close()
    return data


def blank_pdf() -> bytes:
    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def signature_pdf(*, ink: bool = True, faint: bool = False) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for _ in range(25):
        page.insert_text((54, y), TEXT[:90], fontsize=11)
        y += 16
    page.insert_text((54, 760), "Authorized Signature: ______________________", fontsize=11)
    if ink:
        width = 1.5 if faint else 2.5
        color = (0.75, 0.75, 0.75) if faint else (0, 0, 0)
        pts = [(220 + i * 6, 750 + (12 if i % 2 else -12) * (1 if i % 3 else 0.5)) for i in range(25)]
        for a, b in zip(pts, pts[1:]):
            page.draw_line(a, b, color=color, width=width)
    data = doc.tobytes()
    doc.close()
    return data


def image_of(pdf: bytes, dpi: int, fmt: str = "png") -> bytes:
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        return doc[0].get_pixmap(dpi=dpi).tobytes(fmt)


def solid_image(width: int, height: int, fmt: str) -> bytes:
    pm = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), False)
    pm.clear_with(255)
    return pm.tobytes(fmt)


def page_text(pdf: bytes) -> str:
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        return doc[0].get_text()


# --------------------------------------------------------------------------- #
# probe_visual_quality
# --------------------------------------------------------------------------- #


class TestProbe:
    def test_crisp_text_pdf_has_no_flags(self):
        pages = probe_visual_quality(crisp_pdf(), "crisp.pdf")
        assert len(pages) == 1
        page = pages[0]
        assert page["page"] == 1
        assert page["flags"] == []
        assert page["dpi_estimate"] is None  # vector text has no DPI — never invented
        assert page["blur_variance"] >= qp.BLUR_VARIANCE_MIN
        assert page["contrast_range"] >= qp.CONTRAST_RANGE_MIN
        assert "not detected in V1" in page["basis"]
        assert "dpi not applicable" in page["basis"]

    def test_sparse_crisp_page_is_not_low_contrast(self):
        # Three lines of black text: tiny std (mostly paper) but full contrast.
        page = probe_visual_quality(crisp_pdf(lines=3), "sparse.pdf")[0]
        assert page["contrast_std"] < qp.CONTRAST_STD_MIN
        assert "low_contrast" not in page["flags"]

    def test_low_resolution_raster_flagged_low_res(self):
        page = probe_visual_quality(rerender_pdf(crisp_pdf(), 40), "scan40.pdf")[0]
        assert "low_res" in page["flags"]
        assert 35 <= page["dpi_estimate"] <= 45
        assert page["width_px"] == 331  # the embedded raster's own pixel grid
        assert "embedded raster" in page["basis"]
        # A hard-edged low-dpi raster is not blur: the pixels are sharp.
        assert "blurry" not in page["flags"]

    def test_adequate_resolution_raster_not_flagged(self):
        page = probe_visual_quality(rerender_pdf(crisp_pdf(), 300), "scan300.pdf")[0]
        assert page["flags"] == []
        assert 295 <= page["dpi_estimate"] <= 305

    def test_soft_raster_flagged_blurry(self):
        page = probe_visual_quality(soft_pdf(crisp_pdf(), 25, 200), "blurry.pdf")[0]
        assert "blurry" in page["flags"]
        assert "low_res" not in page["flags"]  # nominal 200 dpi: the problem is focus
        assert page["blur_variance"] < qp.BLUR_VARIANCE_MIN

    def test_low_contrast_flagged_and_not_called_blurry(self):
        page = probe_visual_quality(low_contrast_pdf(), "gray.pdf")[0]
        assert "low_contrast" in page["flags"]
        assert page["contrast_range"] < qp.CONTRAST_RANGE_MIN
        # Crisp gray text has a low raw Laplacian (it scales with contrast²);
        # the contrast-normalised guard keeps "blurry" off a sharp page.
        lighter = probe_visual_quality(low_contrast_pdf(0.9, 0.7), "gray2.pdf")[0]
        assert "low_contrast" in lighter["flags"]
        assert "blurry" not in lighter["flags"]

    def test_blank_page_is_measured_but_not_flagged(self):
        page = probe_visual_quality(blank_pdf(), "blank.pdf")[0]
        assert page["flags"] == []
        assert page["blur_variance"] is None
        assert "blank page" in page["basis"]

    def test_png_image_is_one_page_with_native_pixels(self):
        pages = probe_visual_quality(image_of(crisp_pdf(), 40), "scan.png")
        assert len(pages) == 1
        page = pages[0]
        assert (page["width_px"], page["height_px"]) == (331, 468)
        assert "low_res" in page["flags"]
        # dpi from the file's metadata when present, else the stated assumption.
        hi = probe_visual_quality(image_of(crisp_pdf(), 300), "scan300.png")[0]
        assert hi["dpi_estimate"] == 300.0
        assert "metadata" in hi["basis"]
        assert hi["flags"] == []

    def test_jpeg_and_soft_png(self):
        jpg = probe_visual_quality(image_of(crisp_pdf(), 200, "jpg"), "photo.jpg")
        assert len(jpg) == 1 and jpg[0]["flags"] == []
        soft = probe_visual_quality(image_of(soft_pdf(crisp_pdf(), 20, 200), 200), "soft.png")[0]
        assert "blurry" in soft["flags"]

    @pytest.mark.parametrize(
        "data,name",
        [(b"%PDF-1.4 fake", "a.pdf"), (b"hello world", "a.txt"), (b"\x00\x01\x02", "a.png"), (b"", "a.pdf"), (b"junk", "a.bin")],
    )
    def test_unrenderable_input_returns_empty_not_fake_pages(self, data, name):
        assert probe_visual_quality(data, name) == []

    def test_max_pages_respected(self):
        pages = probe_visual_quality(crisp_pdf(pages=5), "five.pdf", max_pages=2)
        assert [p["page"] for p in pages] == [1, 2]

    def test_only_detected_flags_are_ever_emitted(self):
        fixtures = [
            crisp_pdf(),
            rerender_pdf(crisp_pdf(), 40),
            soft_pdf(crisp_pdf()),
            low_contrast_pdf(),
            blank_pdf(),
            signature_pdf(),
        ]
        for data in fixtures:
            for page in probe_visual_quality(data, "x.pdf"):
                assert set(page["flags"]) <= set(qp.DETECTED_FLAGS)
                assert not set(page["flags"]) & set(qp.UNDETECTED_FLAGS)

    def test_fifty_pages_within_budget(self):
        data = crisp_pdf(pages=50)
        start = time.perf_counter()
        pages = probe_visual_quality(data, "big.pdf")
        elapsed = time.perf_counter() - start
        assert len(pages) == 50
        # Measured 0.7 s on a laptop (2026-09-25); generous for CI runners.
        assert elapsed < 4.0, f"probe took {elapsed:.2f}s for 50 pages"


# --------------------------------------------------------------------------- #
# scores
# --------------------------------------------------------------------------- #


class TestScores:
    def test_penalty_tables_match_spec(self):
        assert NUMBER_PENALTIES == {
            "clear": 1.0,
            "printed_good": 1.0,
            "handwritten": 0.7,
            "faded": 0.6,
            "typewritten_low_quality": 0.8,
            "unreadable": 0.0,
            "unknown": 1.0,
        }
        # 2026-09-27 vocabulary + the legacy words for reports saved before it.
        assert SIGNATURE_PENALTIES == {
            "clear": 1.0, "present_clear": 1.0, "faint": 0.8, "incomplete": 0.7, "stamped": 0.7, "stamp": 0.7,
            "questionable": 0.6, "present_ambiguous": 0.6, "printed_name": 0.6, "missing": 0.5, "unreadable": 0.4, "unknown": 1.0,
        }

    def test_page_quality_none_without_signal(self):
        score, basis = page_quality_score(
            visual=None, ocr_confidence=None, text_density=None, parse_coverage=None, image_ratio=None, signature_quality=None
        )
        assert score is None
        assert basis == "no_signal"

    def test_page_quality_from_clean_visual_is_one(self):
        visual = probe_visual_quality(crisp_pdf(), "crisp.pdf")[0]
        score, basis = page_quality_score(
            visual=visual, ocr_confidence=None, text_density=None, parse_coverage=None, image_ratio=None, signature_quality=None
        )
        assert score == 1.0
        assert basis.startswith("visual 1.00")

    def test_page_quality_multiplies_available_signals(self):
        """A text-layer page (no OCR figure) multiplies the probe's signals."""
        visual = probe_visual_quality(soft_pdf(crisp_pdf()), "blurry.pdf")[0]
        score, basis = page_quality_score(
            visual=visual, ocr_confidence=None, text_density=0.9, parse_coverage=None, image_ratio=0.3, signature_quality="faint"
        )
        assert 0.0 < score < 0.8  # density 0.9 is above the floor: factor 1.0
        assert "blurry" in basis and "density 0.90→1.00" in basis
        assert "signature_faint 0.80" in basis
        assert "image_ratio 0.30, informative" in basis
        assert "coverage" not in basis  # a missing signal is not credited

    def test_an_ocr_read_page_is_scored_by_ocr_confidence_times_coverage(self):
        """User decision 2026-09-29: the reader's own figures, not the probe's —
        a crisp CMS-1500 read by Textract at 0.96 had scored 0.58 because its
        signature box was ambiguous. Probe and signature stay informative."""
        visual = probe_visual_quality(soft_pdf(crisp_pdf()), "blurry.pdf")[0]
        score, basis = page_quality_score(
            visual=visual, ocr_confidence=0.96, text_density=0.9, parse_coverage=1.0, image_ratio=0.1, signature_quality="present_ambiguous"
        )
        assert score == 0.96 and basis.startswith("ocr 0.96 × coverage 1.00")
        assert "signature_present_ambiguous" in basis and "informative" in basis and "blurry" in basis
        score2, basis2 = page_quality_score(
            visual=None, ocr_confidence=0.8, text_density=None, parse_coverage=0.5, image_ratio=None, signature_quality=None
        )
        assert score2 == 0.4 and basis2 == "ocr 0.80 × coverage 0.50"
        score3, _ = page_quality_score(visual=None, ocr_confidence=0.8, text_density=None, parse_coverage=None, image_ratio=None, signature_quality=None)
        assert score3 == 0.8  # coverage unknown: not credited, not penalised

    def test_page_quality_density_is_a_floor_not_a_factor(self):
        """A one-paragraph page is not lower quality than a dense one (2026-09-25:
        a crisp declarations PDF scored 0.146 on density alone)."""
        visual = probe_visual_quality(crisp_pdf(), "crisp.pdf")[0]
        full, _ = page_quality_score(
            visual=visual, ocr_confidence=None, text_density=0.15, parse_coverage=1.0, image_ratio=None, signature_quality=None
        )
        assert full == 1.0
        sparse, basis = page_quality_score(
            visual=visual, ocr_confidence=None, text_density=0.01, parse_coverage=1.0, image_ratio=None, signature_quality=None
        )
        assert sparse == 0.2 and "density 0.01→0.20" in basis

    def test_page_quality_ignores_missing_signature(self):
        """An unsigned page is a compliance fact, not a legibility fact."""
        visual = probe_visual_quality(crisp_pdf(), "crisp.pdf")[0]
        score, basis = page_quality_score(
            visual=visual, ocr_confidence=None, text_density=None, parse_coverage=1.0, image_ratio=None, signature_quality="missing"
        )
        assert score == 1.0 and "signature" not in basis
        faint, basis = page_quality_score(
            visual=visual, ocr_confidence=None, text_density=None, parse_coverage=1.0, image_ratio=None, signature_quality="faint"
        )
        assert faint == 0.8 and "signature_faint 0.80" in basis

    def test_spec_example_confidence_and_basis(self):
        value, basis = quality_weighted_confidence(
            parser_confidence=0.95,
            parser_name="jdf-cli",
            page_quality=0.40,
            number_quality="handwritten",
            z3_violation=True,
            signature_quality=None,
        )
        assert value == 0.24
        assert basis == (
            "parser_confidence (0.95) × page_quality (0.40) × handwritten_penalty (0.70) × z3_penalty (0.90) = 0.24"
        )

    def test_no_signal_is_conservative_default(self):
        assert quality_weighted_confidence(
            parser_confidence=None, parser_name="", page_quality=None, number_quality=None, z3_violation=False, signature_quality=None
        ) == (0.50, "no_signal_available — conservative default")

    def test_parser_defaults_when_only_parser_known(self):
        jdf, basis = quality_weighted_confidence(
            parser_confidence=None, parser_name="jdf-cli", page_quality=None, number_quality=None, z3_violation=False, signature_quality=None
        )
        assert jdf == pytest.approx(0.85 * 0.5, abs=0.01)
        assert "parser_default[jdf-cli] (0.85)" in basis and "page_quality_neutral (0.50)" in basis
        textract, _ = quality_weighted_confidence(
            parser_confidence=None, parser_name="textract", page_quality=None, number_quality=None, z3_violation=False, signature_quality=None
        )
        assert textract == pytest.approx(0.80 * 0.5, abs=0.01)

    def test_signature_penalty_applied(self):
        value, basis = quality_weighted_confidence(
            parser_confidence=1.0, parser_name="textract", page_quality=1.0, number_quality=None, z3_violation=False, signature_quality="missing"
        )
        assert value == 0.5
        assert "signature_missing_penalty (0.50)" in basis


# --------------------------------------------------------------------------- #
# numbers and signatures
# --------------------------------------------------------------------------- #


class TestAssessNumber:
    def test_empty_value_is_unreadable_zero_confidence(self):
        out = assess_number("", ocr_confidence=0.9, page_quality=0.9, handwritten=False)
        assert out["quality"] == "unreadable" and out["penalty"] == 0.0 and out["review_required"] is True
        assert "manual review required" in out["basis"]
        assert assess_number(None, ocr_confidence=None, page_quality=None, handwritten=None)["quality"] == "unreadable"

    def test_handwritten_wins(self):
        out = assess_number("1240", ocr_confidence=0.95, page_quality=0.95, handwritten=True)
        assert (out["quality"], out["penalty"], out["review_required"]) == ("handwritten", 0.7, True)

    def test_faded_from_low_ocr(self):
        out = assess_number("1240", ocr_confidence=0.55, page_quality=0.9, handwritten=False)
        assert (out["quality"], out["penalty"]) == ("faded", 0.6)

    def test_typewritten_low_quality_from_bad_page_with_ok_ocr(self):
        out = assess_number("1240", ocr_confidence=0.9, page_quality=0.4, handwritten=False)
        assert (out["quality"], out["penalty"], out["review_required"]) == ("typewritten_low_quality", 0.8, True)

    def test_printed_good_and_unknown(self):
        assert assess_number("1240", ocr_confidence=0.9, page_quality=0.9, handwritten=False)["quality"] == "printed_good"
        out = assess_number("1240", ocr_confidence=None, page_quality=None, handwritten=None)
        assert out["quality"] == "unknown" and out["penalty"] == 1.0 and out["review_required"] is False


class TestAssessSignature:
    def test_no_label_is_unknown_and_asserts_nothing(self):
        out = assess_signature("Premium USD 1,240.00", visual=None, ocr_lines=None, page=3)
        assert out == {
            "present": None,
            "quality": "unknown",
            "review_required": False,
            "basis": "no signature label in page text; presence not assessable in V1",
            "page": 3,
        }

    def test_label_without_visual_is_unreadable_with_review(self):
        out = assess_signature("Signature: ______", visual=None, ocr_lines=None)
        assert out["quality"] == "unreadable" and out["present"] is None and out["review_required"] is True

    def test_typed_name_on_the_signature_line_is_printed_name(self):
        out = assess_signature("Insured's Signature: John Q. Sample\nDate: 01/01/2025", visual=None, ocr_lines=None)
        assert out["quality"] == "printed_name" and out["present"] is True and out["review_required"] is True

    def test_ocr_lines_supply_the_text(self):
        out = assess_signature("", visual=None, ocr_lines=[{"text": "Signed by the insured"}])
        assert "Signed" in out["basis"]

    def test_stamp_and_esignature_are_not_one_verdict(self):
        out = assess_signature("Authorized signature: [SEAL]", visual=None, ocr_lines=None)
        assert out["quality"] == "stamp" and out["review_required"] is True and out["present"] is True
        out = assess_signature("Authorized signature: electronically signed", visual=None, ocr_lines=None)
        assert out["quality"] == "present_clear" and out["review_required"] is False and out["present"] is True

    def test_signed_page_is_ambiguous_never_clear(self):
        pdf = signature_pdf()
        out = assess_signature(page_text(pdf), visual=probe_visual_quality(pdf, "s.pdf")[0], ocr_lines=None, page=1)
        assert out["quality"] == "present_ambiguous" and out["present"] is True and out["review_required"] is True
        assert "cannot confirm a handwritten signature" in out["basis"]

    def test_faint_signature(self):
        pdf = signature_pdf(faint=True)
        out = assess_signature(page_text(pdf), visual=probe_visual_quality(pdf, "s.pdf")[0], ocr_lines=None)
        assert out["quality"] == "present_ambiguous" and out["present"] is True and out["review_required"] is True
        assert "faint mark" in out["basis"] and "30%" in out["basis"]

    def test_missing_signature(self):
        pdf = signature_pdf(ink=False)
        out = assess_signature(page_text(pdf), visual=probe_visual_quality(pdf, "s.pdf")[0], ocr_lines=None)
        assert out["quality"] == "missing" and out["present"] is False and out["review_required"] is True
        assert "no mark beyond the label" in out["basis"]

    def test_never_clear(self):
        for pdf in (signature_pdf(), signature_pdf(faint=True), signature_pdf(ink=False)):
            out = assess_signature(page_text(pdf), visual=probe_visual_quality(pdf, "s.pdf")[0], ocr_lines=None)
            assert out["quality"] != "clear"


# --------------------------------------------------------------------------- #
# detect_material
# --------------------------------------------------------------------------- #


class TestDetectMaterial:
    def test_digital_and_scanned_pdf(self):
        digital = detect_material(crisp_pdf(), "policy.pdf")
        assert (digital["material_type"], digital["modality"], digital["source_kind"]) == ("pdf", "digital_pdf", "pdf")
        scanned = detect_material(rerender_pdf(crisp_pdf(), 150), "scan.pdf")
        assert (scanned["material_type"], scanned["modality"], scanned["source_kind"]) == ("pdf", "scanned_pdf", "scanned")

    def test_text_file(self):
        out = detect_material(b"hello", "notes.md")
        assert (out["material_type"], out["modality"], out["source_kind"]) == ("text_file", "text", "text")

    def test_document_image(self):
        out = detect_material(image_of(crisp_pdf(), 150), "scan.png")
        assert (out["material_type"], out["source_kind"]) == ("image", "image")
        assert "treated as a scanned page" in out["basis"]

    def test_camera_shaped_jpeg_is_photo(self):
        out = detect_material(solid_image(2000, 1500, "jpg"), "IMG_0042.jpg")
        assert (out["material_type"], out["modality"], out["source_kind"]) == ("photo", "phone_photo", "photo")

    def test_screen_shaped_png_is_screenshot(self):
        out = detect_material(solid_image(1920, 1080, "png"), "Screenshot.png")
        assert (out["material_type"], out["modality"], out["source_kind"]) == ("screenshot", "screenshot", "image")

    def test_hints_are_honoured(self):
        assert detect_material(b"x", "bundle.pdf", source_kind="mixed")["material_type"] == "mixed_bundle"
        assert detect_material(image_of(crisp_pdf(), 72), "p.png", source_kind="photo")["modality"] == "phone_photo"
        assert detect_material(crisp_pdf(), "p.pdf", source_kind="scanned")["modality"] == "scanned_pdf"
        assert detect_material(b"%PDF-1.4", "p.pdf", source_kind="text")["material_type"] == "text_file"

    def test_never_guesses_handwriting_tables_or_bundles(self):
        for data, name in (
            (crisp_pdf(), "a.pdf"),
            (image_of(crisp_pdf(), 150), "a.png"),
            (solid_image(2000, 1500, "jpg"), "a.jpg"),
            (b"junk", "a.bin"),
        ):
            out = detect_material(data, name)
            assert out["material_type"] not in ("handwritten_image", "table", "mixed_bundle")
            assert out["modality"] not in ("handwritten", "table_image", "mixed")


# --------------------------------------------------------------------------- #
# ink map and the signature band by the label element (2026-09-28)
# --------------------------------------------------------------------------- #


def mid_page_signature_pdf(*, ink: bool, typed: str | None = None) -> bytes:
    """The bench declarations shape: body text, a "Countersigned …" line, then
    ``Authorized Signature: ____`` at ≈ 45 % of the page height with, when
    ``ink``, a vector stroke over the ruled line — nothing in the bottom 20 %."""
    doc = fitz.open()
    page = doc.new_page()  # 612 × 792
    y = 72
    for _ in range(18):
        page.insert_text((54, y), TEXT[:90], fontsize=10)
        y += 13.5
    page.insert_text((54, y), "Countersigned this 20th day of February, 2025.", fontsize=10)
    y += 13.5
    label = "Authorized Signature: " + (typed if typed else "______________________________")
    page.insert_text((54, y), label, fontsize=10)
    rect = page.search_for("Authorized Signature")[0]
    x0, base = rect.x1 + 6, rect.y1 - 1
    if not typed:
        page.draw_line((x0, base), (x0 + 220, base), color=(0, 0, 0), width=0.6)
    if ink:
        pts = [(x0 + 8 + t * 4.5, base - 4 - 9 * abs(((t / 40 * 6) % 2) - 1)) for t in range(41)]
        page.draw_polyline(pts, color=(0.05, 0.05, 0.25), width=1.6)
    data = doc.tobytes()
    doc.close()
    return data


def text_and_layout(pdf: bytes) -> tuple[str, list[dict]]:
    """Page text and a ``page_layout``-shaped segment list from PyMuPDF's text
    blocks (one segment per block, relative bbox) — what the extractor would
    hand ``assess_signature`` for a text-layer page."""
    doc = fitz.open(stream=pdf, filetype="pdf")
    page = doc[0]
    w, h = page.rect.width, page.rect.height
    segments, texts, cursor = [], [], 0
    for x0, y0, x1, y1, text, *_ in page.get_text("blocks"):
        text = text.rstrip("\n")
        if not text.strip():
            continue
        segments.append({"start": cursor, "end": cursor + len(text), "text": text, "node_id": None, "chunk_id": None,
                         "bbox": [round(x0 / w, 4), round(y0 / h, 4), round(x1 / w, 4), round(y1 / h, 4)], "elements": None})
        texts.append(text)
        cursor += len(text) + 1
    doc.close()
    return "\n".join(texts), segments


class TestInkMap:
    def test_ink_map_is_measured_and_decodes(self):
        visual = probe_visual_quality(crisp_pdf(lines=30), "c.pdf")[0]
        m = visual["ink_map"]
        assert m["cols"] == qp.INK_MAP_COLS and m["rows"] > 0 and m["cell_px"] > 0 and m["dpi"] == qp.HIRES_DPI
        assert len(m["ink"]) == len(m["mark"]) == 2 * m["cols"] * m["rows"]
        whole = qp.ink_map_region(visual, [0.0, 0.0, 1.0, 1.0])
        assert whole["cells"] == m["cols"] * m["rows"]
        # the text sits in the upper part of the page; the bottom fifth is paper
        assert whole["mark"] > 0.005 and whole["ink"] <= whole["mark"]
        assert qp.ink_map_region(visual, [0.0, 0.85, 1.0, 1.0])["mark"] == 0.0
        assert qp.ink_map_region(visual, [0.0, 0.0, 0.001, 0.001]) is None  # no cell centre inside
        assert qp.ink_map_region({"ink_map": None}, [0, 0, 1, 1]) is None
        assert qp.ink_map_region(None, [0, 0, 1, 1]) is None

    def test_straight_rules_are_erased_from_the_map_but_not_from_the_page_ratios(self):
        doc = fitz.open()
        page = doc.new_page()
        page.draw_line((72, 396), (540, 396), color=(0, 0, 0), width=1.0)   # horizontal rule across the middle
        page.draw_line((306, 100), (306, 700), color=(0, 0, 0), width=1.0)  # vertical rule
        visual = probe_visual_quality(doc.tobytes(), "rules.pdf")[0]
        assert visual["mark_ratio"] > 0.0  # the page ratios still see the rules
        m = visual["ink_map"]
        assert m["rules_erased"]["horizontal"] >= 1 and m["rules_erased"]["vertical"] >= 1
        assert qp.ink_map_region(visual, [0.0, 0.0, 1.0, 1.0])["mark"] == 0.0


class TestSignatureBand:
    def test_stroke_beside_a_mid_page_label_is_found_by_the_label_element(self):
        pdf = mid_page_signature_pdf(ink=True)
        text, layout = text_and_layout(pdf)
        visual = probe_visual_quality(pdf, "s.pdf")[0]
        out = assess_signature(text, visual=visual, ocr_lines=None, page=1, layout=layout)
        assert out["quality"] == "present_ambiguous" and out["present"] is True and out["review_required"] is True
        region = out["region"]
        assert region["basis"] == "label element" and region["page"] == 1 and region["cells"] > 0
        x0, y0, x1, y1 = region["bbox"]
        # the label line (≈ 41 % of the page height; PyMuPDF's block may fold the
        # line above into the same block), never the bottom band
        assert 0.25 < x0 < 0.45 and 0.33 < y0 < 0.50 and y1 < 0.60 and x1 > x0
        assert "band right of the label" in out["basis"] and "cannot confirm a handwritten signature" in out["basis"]
        # the same page judged by the bottom band alone: blank paper, "missing" — the gap the band closes
        old = assess_signature(text, visual=visual, ocr_lines=None, page=1, layout=None)
        assert old["quality"] == "missing" and old["region"]["basis"] == "bottom band"
        assert old["region"]["bbox"] == [0.0, 0.8, 1.0, 1.0] and "no layout bbox" in old["region"]["reason"]

    def test_blank_mid_page_line_is_missing_and_the_ruled_line_is_not_a_mark(self):
        pdf = mid_page_signature_pdf(ink=False)
        text, layout = text_and_layout(pdf)
        out = assess_signature(text, visual=probe_visual_quality(pdf, "s.pdf")[0], ocr_lines=None, page=1, layout=layout)
        assert out["quality"] == "missing" and out["present"] is False and out["review_required"] is True
        assert out["region"]["basis"] == "label element" and out["region"]["mark_ratio"] < qp.SIGNATURE_BAND_BLANK_MARK
        assert "no mark beyond the label" in out["basis"]

    def test_typed_name_on_a_mid_page_line_is_printed_name(self):
        pdf = mid_page_signature_pdf(ink=False, typed="Marianne Costa")
        text, layout = text_and_layout(pdf)
        out = assess_signature(text, visual=probe_visual_quality(pdf, "s.pdf")[0], ocr_lines=None, page=1, layout=layout)
        assert out["quality"] == "printed_name" and out["present"] is True
        assert out["region"]["basis"] == "label element" and "after the typed name" in out["region"]["band"]

    def test_label_picker_prefers_the_signature_line_over_countersigned(self):
        text = "Countersigned this 20th day of February, 2025.\nAuthorized Signature: ______"
        picked = qp._pick_label(text)
        assert picked.group(0) == "Authorized Signature"
        assert qp._pick_label("Signed by the insured").group(0) == "Signed by"
        assert qp._pick_label("Countersigned today") is None  # word-bounded: not a bare "signed"

    def test_bottom_band_fallback_still_reports_its_region(self):
        for pdf, quality in ((signature_pdf(), "present_ambiguous"), (signature_pdf(ink=False), "missing")):
            out = assess_signature(page_text(pdf), visual=probe_visual_quality(pdf, "s.pdf")[0], ocr_lines=None, page=1)
            assert out["quality"] == quality and out["region"]["basis"] == "bottom band"

    def test_verdicts_without_a_visual_have_no_region(self):
        out = assess_signature("Authorized signature: electronically signed", visual=None, ocr_lines=None)
        assert out["quality"] == "present_clear" and "region" not in out
