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

    def test_scanned_pdf_selects_textract(self):
        assert select_parser(_image_only_pdf(), filename="scan.pdf") == "textract"

    def test_source_kind_scanned_overrides_probe(self):
        # A client that knows the document is scanned forces Textract — even
        # for bytes that would probe as a clean text PDF.
        assert select_parser(_pdf_with_text(), source_kind="scanned") == "textract"

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

    def test_source_kind_none_and_unknown_probe(self):
        # An unrecognized source_kind is not an override: probing decides.
        data = _image_only_pdf()
        assert select_parser(data, filename="scan.pdf", source_kind="weird") == "textract"
        assert select_parser(data, filename="scan.pdf", source_kind=None) == "textract"

    def test_empty_filename_pages_still_probe(self):
        # filename None on pdf-ish bytes: default branch, no probe, jdf.
        assert select_parser(b"%PDF-1.4") == "jdf"

    def test_multiline_empty_probe_selects_textract(self):
        # A 4-page PDF whose first 3 pages are empty is a scan: the probe
        # reads only the first pages and decides textract.
        import fitz

        doc = fitz.open()
        for _ in range(4):
            page = doc.new_page()
            page.insert_text((72, 72), " ")  # whitespace-only page
        data = doc.tobytes()
        doc.close()
        assert _probe_pdf_for_parser(data) == "textract"