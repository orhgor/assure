"""Parser routing: select_parser decides the backend, callers execute it."""

from __future__ import annotations

import pytest

from prompt_matrix.services.parser_router import (
    _TEXT_LIKE_EXTENSIONS,
    _probe_pdf_for_parser,
    select_parser,
)


def _pdf_with_text(page_text: str = "Hello source document.") -> bytes:
    """A real PDF with a text layer (PyMuPDF is already a dependency)."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), page_text)
    data = doc.tobytes()
    doc.close()
    return data


def _image_only_pdf() -> bytes:
    """A PDF with page content but no text layer — a scan in PDF clothing."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    # A rectangle is real page content but carries no text.
    page.draw_rect(fitz.Rect(50, 50, 300, 200), color=(0, 0, 1))
    data = doc.tobytes()
    doc.close()
    return data


class TestParserRouting:
    def test_clean_pdf_selects_jdf(self):
        assert select_parser(_pdf_with_text(), filename="doc.pdf") == "jdf"

    def test_scanned_pdf_selects_jdf_ocr_by_default(self, monkeypatch):
        # A scan goes to JDF CI's bundled OCR first: local, no per-page fee.
        monkeypatch.delenv("PARSER_SCAN_BACKEND", raising=False)
        monkeypatch.delenv("JDF_OCR", raising=False)
        assert select_parser(_image_only_pdf(), filename="scan.pdf") == "jdf-ocr"

    def test_scanned_pdf_selects_textract_when_configured(self, monkeypatch):
        monkeypatch.setenv("PARSER_SCAN_BACKEND", "textract")
        assert select_parser(_image_only_pdf(), filename="scan.pdf") == "textract"

    def test_ocr_disabled_sends_scans_to_textract(self, monkeypatch):
        # JDF CI without OCR would return empty pages for a scan, so turning
        # OCR off must route the scan to Textract, not to an empty parse.
        monkeypatch.delenv("PARSER_SCAN_BACKEND", raising=False)
        monkeypatch.setenv("JDF_OCR", "none")
        assert select_parser(_image_only_pdf(), filename="scan.pdf") == "textract"

    def test_source_kind_scanned_overrides_probe(self, monkeypatch):
        # A client that knows the document is scanned forces the scan backend
        # — even for bytes that would probe as a clean text PDF.
        monkeypatch.setenv("PARSER_SCAN_BACKEND", "textract")
        assert select_parser(_pdf_with_text(), source_kind="scanned") == "textract"
        monkeypatch.delenv("PARSER_SCAN_BACKEND", raising=False)
        monkeypatch.delenv("JDF_OCR", raising=False)
        assert select_parser(_pdf_with_text(), source_kind="scanned") == "jdf-ocr"

    def test_source_kind_text_returns_jdf_with_explicit_meaning(self):
        # Returns "jdf" to signal "caller should wrap text as JDF", never to
        # mean "run binary parsing on this".
        assert select_parser(b"hello world", source_kind="text") == "jdf"

    def test_text_file_without_source_kind(self):
        assert select_parser(b"hello world", filename="doc.txt") == "jdf"

    def test_text_file_skips_pdf_probe(self):
        # A text-named file must not be probed as a PDF: routing by extension
        # happens before any binary detection runs.
        assert select_parser(b"%PDF-1.4 fake but named .txt", filename="doc.txt") == "jdf"

    def test_all_text_like_extensions_skip_probing(self):
        for ext in _TEXT_LIKE_EXTENSIONS:
            assert select_parser(b"%PDF-1.4 whatever", filename=f"doc.{ext}") == "jdf"

    def test_default_to_jdf_for_unknown(self):
        assert select_parser(b"any binary", filename="unknown.bin") == "jdf"

    def test_no_filename_defaults_to_jdf(self):
        assert select_parser(b"any bytes") == "jdf"

    def test_probe_fails_to_jdf(self, monkeypatch):
        import fitz

        class Boom:
            def __enter__(self):
                raise RuntimeError("corrupt")

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(fitz, "open", lambda *a, **k: Boom())
        # A corrupt PDF is not proof of a scan: default to JDF and let the
        # parse fail naturally through the caller's fallback.
        assert _probe_pdf_for_parser(b"%PDF-1.4 broken") == "jdf"

    def test_source_kind_none_and_unknown_probe(self, monkeypatch):
        # An unrecognized source_kind is not an override: probing decides.
        monkeypatch.setenv("PARSER_SCAN_BACKEND", "textract")
        data = _image_only_pdf()
        assert select_parser(data, filename="scan.pdf", source_kind="weird") == "textract"
        assert select_parser(data, filename="scan.pdf", source_kind=None) == "textract"

    def test_empty_filename_pages_still_probe(self):
        # filename None on pdf-ish bytes: default branch, no probe, jdf.
        assert select_parser(b"%PDF-1.4") == "jdf"

    def test_multiline_empty_probe_selects_scan_backend(self, monkeypatch):
        # A 4-page PDF whose first 3 pages are empty is a scan: the probe
        # reads only the first pages and decides the scan backend.
        monkeypatch.setenv("PARSER_SCAN_BACKEND", "textract")
        import fitz

        doc = fitz.open()
        for _ in range(4):
            page = doc.new_page()
            page.insert_text((72, 72), " ")  # whitespace-only page
        data = doc.tobytes()
        doc.close()
        assert _probe_pdf_for_parser(data) == "textract"

class TestMultimodalRouting:
    """Images have no text layer: the router sends them to the scan backend
    without a probe, and ``route_intake`` is the one place Laya is consulted."""

    @pytest.mark.parametrize("ext", ["png", "jpg", "jpeg", "tif", "tiff", "bmp", "PNG", "Jpg"])
    def test_image_extensions_select_scan_backend(self, ext, monkeypatch):
        monkeypatch.delenv("PARSER_SCAN_BACKEND", raising=False)
        monkeypatch.delenv("JDF_OCR", raising=False)
        assert select_parser(b"\x89PNG anything", filename=f"photo.{ext}") == "jdf-ocr"
        monkeypatch.setenv("PARSER_SCAN_BACKEND", "textract")
        assert select_parser(b"\x89PNG anything", filename=f"photo.{ext}") == "textract"

    def test_image_is_not_probed_as_pdf(self, monkeypatch):
        import fitz

        def boom(*a, **k):
            raise AssertionError("an image must not be opened by the PDF probe")

        monkeypatch.setattr(fitz, "open", boom)
        monkeypatch.setenv("PARSER_SCAN_BACKEND", "textract")
        assert select_parser(b"%PDF-1.4 named like an image", filename="x.png") == "textract"

    def test_is_image_filename(self):
        from prompt_matrix.services.parser_router import is_image_filename

        assert is_image_filename("a.PNG") and is_image_filename("dir/b.tiff")
        assert not is_image_filename("a.pdf") and not is_image_filename("noext") and not is_image_filename(None)

    def test_route_intake_shape(self, monkeypatch):
        from prompt_matrix.services.parser_router import route_intake

        monkeypatch.delenv("PARSER_SCAN_BACKEND", raising=False)
        out = route_intake(_pdf_with_text(), "doc.pdf")
        assert set(out) >= {"parser", "material_type", "modality", "source_kind", "visual_pages", "laya"}
        assert out["parser"] == select_parser(_pdf_with_text(), "doc.pdf") == "jdf"
        assert out["material_type"] == "pdf" and out["modality"] == "digital_pdf"
        assert len(out["visual_pages"]) == 1
        assert out["laya"]["model"] == "rules-v1" and out["laya"]["policy_version"] == "v1"

    def test_route_intake_unrenderable_bytes_have_no_fake_pages(self):
        from prompt_matrix.services.parser_router import route_intake

        out = route_intake(b"%PDF-1.4 fake", "doc.pdf")
        assert out["parser"] == "jdf"
        assert out["visual_pages"] == []
        assert out["laya"]["human_review"] is True
