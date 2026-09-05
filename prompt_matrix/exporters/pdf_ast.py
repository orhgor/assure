"""PDF export for JDF documents (WeasyPrint → Playwright → minimal text PDF)."""

from __future__ import annotations

from typing import Any

try:
    from .text_ast import jdf_to_html
except ImportError:
    from exporters.text_ast import jdf_to_html


def _pdf_via_weasyprint(html: str) -> bytes | None:
    try:
        from weasyprint import HTML  # type: ignore[import-untyped]
    except Exception:
        return None
    try:
        return HTML(string=html).write_pdf()
    except Exception:
        return None


def _pdf_via_playwright(html: str) -> bytes | None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            pdf = page.pdf(format="A4", print_background=True)
            browser.close()
            return pdf
    except Exception:
        return None


def _pdf_via_simple(tree: dict[str, Any]) -> bytes:
    try:
        from ..history import _simple_pdf
        from ..exporters.text_ast import jdf_to_markdown
    except ImportError:
        from history import _simple_pdf
        from exporters.text_ast import jdf_to_markdown

    title = str(
        (tree.get("meta") or {}).get("title") or tree.get("document_id") or "Assure Document"
    )
    body = jdf_to_markdown(tree)
    return _simple_pdf(title, body)


def jdf_to_pdf_bytes(tree: dict[str, Any]) -> tuple[bytes, str]:
    """Return (pdf_bytes, engine_used)."""
    html = jdf_to_html(tree)
    pdf = _pdf_via_weasyprint(html)
    if pdf:
        return pdf, "weasyprint"
    pdf = _pdf_via_playwright(html)
    if pdf:
        return pdf, "playwright"
    return _pdf_via_simple(tree), "simple"


def export_jdf_to_pdf(tree: dict[str, Any]) -> bytes:
    data, _engine = jdf_to_pdf_bytes(tree)
    return data
