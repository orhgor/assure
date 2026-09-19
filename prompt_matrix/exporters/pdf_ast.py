"""PDF export for JDF documents (WeasyPrint → Playwright → paginated text PDF).

Both HTML engines are optional and both degrade quietly by design, which is how
the deployed dossier shipped for a whole session as memo text: the box's venv
carries neither package, so ``_pdf_via_weasyprint`` and ``_pdf_via_playwright``
each returned None on an ImportError and nothing said so. The probes now record
why they were skipped (``pdf_engine_notes``) and log it, and the fallback carries
the sections the HTML bundle produces rather than the memo alone.
"""

from __future__ import annotations

import logging
import re
from html import unescape as _unescape
from typing import Any

try:
    from .text_ast import jdf_to_html, jdf_to_markdown
except ImportError:
    from exporters.text_ast import jdf_to_html, jdf_to_markdown

_log = logging.getLogger(__name__)

#: ``{engine: why it was skipped}``, written by the probe and read by the caller
#: that has to tell a reader the artifact was not rendered by an HTML engine.
_SKIPPED_ENGINES: dict[str, str] = {}


def _note_skip(engine: str, exc: BaseException) -> None:
    reason = f"{type(exc).__name__}: {exc}"
    _SKIPPED_ENGINES[engine] = reason
    _log.warning(
        "PDF engine %s is not usable here (%s); the text fallback renders this document",
        engine,
        reason,
    )


def _note_used(engine: str) -> None:
    _SKIPPED_ENGINES.pop(engine, None)


def pdf_engine_notes() -> dict[str, str]:
    """Why each HTML engine was skipped on the last attempt (engine → reason).

    Empty for an engine that rendered, so a caller can distinguish "tried and
    could not" from "never tried" — the state the export used to hide.
    """
    return dict(_SKIPPED_ENGINES)


def _pdf_via_weasyprint(html: str) -> bytes | None:
    try:
        from weasyprint import HTML  # type: ignore[import-untyped]
    except Exception as exc:
        _note_skip("weasyprint", exc)
        return None
    try:
        pdf = HTML(string=html).write_pdf()
    except Exception as exc:
        _note_skip("weasyprint", exc)
        return None
    _note_used("weasyprint")
    return pdf


def _pdf_via_playwright(html: str) -> bytes | None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        _note_skip("playwright", exc)
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            pdf = page.pdf(format="A4", print_background=True)
            browser.close()
    except Exception as exc:
        _note_skip("playwright", exc)
        return None
    _note_used("playwright")
    return pdf


# ---------------------------------------------------------------------------
# The text fallback: the same sections, without an HTML engine.
#
# The writer below is the module's existing one — the same five PDF objects and
# the same Helvetica core font, no library and no system package — with its
# single-page 90-line cap removed. The cap was the other half of this defect: the
# audit bundle's text was cut off mid-document, so the sign-offs, the lock hash
# and the appendix (the sections after the body) never reached the page at all.
# ---------------------------------------------------------------------------

_PAGE_WIDTH = 612
_PAGE_HEIGHT = 792
_MARGIN_X = 48
_FIRST_BASELINE_Y = 756
_LAST_BASELINE_Y = 60
_LINE_LEADING = 13
_LINE_CHARS = 92
_FONT_SIZE = 10

#: Typographic characters the core font cannot carry, mapped to what it can, so a
#: quotation from a policy still reads as a quotation instead of a row of "?".
_ASCII_MAP = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": ",",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
        "\u00a0": " ",
        "\u2022": "*",
        "\u00b7": "-",
        "\u2192": "->",
        "\u2264": "<=",
        "\u2265": ">=",
    }
)

_DROP_ELEMENTS = re.compile(r"(?is)<(script|style|head)\b.*?</\1\s*>")
_LINE_BREAKS = re.compile(r"(?i)<(br|/p|/h[1-6]|/tr|/li|/pre|/blockquote|/div|/table|/section)\b[^>]*>")
_LIST_ITEMS = re.compile(r"(?i)<li\b[^>]*>")
_CELLS = re.compile(r"(?i)</t[dh]\s*>\s*")
_TAGS = re.compile(r"(?s)<[^>]*>")
_BLANK_RUNS = re.compile(r"\n{3,}")


def html_to_text(markup: str) -> str:
    """The readable text of an HTML document, block by block.

    The audit bundle's fallback is produced this way on purpose: the text cannot
    drift from the HTML the export also serves, because it *is* that HTML with the
    tags taken off.
    """
    text = _DROP_ELEMENTS.sub("", markup or "")
    text = _LIST_ITEMS.sub("\n- ", text)
    text = _CELLS.sub("  |  ", text)
    text = _LINE_BREAKS.sub("\n", text)
    text = _TAGS.sub("", text)
    text = _unescape(text).translate(_ASCII_MAP)
    lines = [line.rstrip() for line in text.splitlines()]
    return _BLANK_RUNS.sub("\n\n", "\n".join(lines)).strip()


def _pdf_string(value: str) -> str:
    """A PDF literal string: ASCII only, with the delimiters escaped."""
    cleaned = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in (value or ""))
    return cleaned.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap(text: str, width: int = _LINE_CHARS) -> list[str]:
    lines: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        while len(line) > width:
            cut = line.rfind(" ", 0, width)
            if cut <= 0:
                cut = width
            lines.append(line[:cut])
            line = line[cut:].lstrip()
        lines.append(line)
    return lines


def text_pdf(title: str, body: str) -> bytes:
    """A paginated, ASCII Helvetica PDF carrying every line of ``body``.

    No offset limit, unlike the single-page writer this replaces: a bundle's
    sections all reach the file, and each page is numbered so a reader can tell a
    page was rendered rather than dropped.
    """
    lines = _wrap(f"{title}\n\n{body}") if title else _wrap(body)
    per_page = max(1, (_FIRST_BASELINE_Y - _LAST_BASELINE_Y) // _LINE_LEADING)
    pages = [lines[i : i + per_page] for i in range(0, len(lines), per_page)] or [[]]
    total = len(pages)

    font_object = 3 + 2 * total
    kids = " ".join(f"{3 + 2 * index + 1} 0 R" for index in range(total))
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {total} >>".encode("ascii"),
    ]
    for index, page_lines in enumerate(pages, start=1):
        commands = ["BT", f"/F1 {_FONT_SIZE} Tf", f"{_LINE_LEADING} TL"]
        commands.append(f"{_MARGIN_X} {_FIRST_BASELINE_Y} Td")
        for line_index, line in enumerate(page_lines):
            if line_index:
                commands.append("T*")
            commands.append(f"({_pdf_string(line)}) Tj")
        commands.append("ET")
        commands.append("BT")
        commands.append(f"/F1 8 Tf")
        commands.append(f"{_MARGIN_X} {_LAST_BASELINE_Y - 14} Td")
        commands.append(f"(Page {index} of {total}) Tj")
        commands.append("ET")
        stream = "\n".join(commands).encode("ascii", "replace")
        objects.append(
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream"
        )
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_WIDTH} {_PAGE_HEIGHT}] "
                f"/Contents {3 + 2 * (index - 1)} 0 R "
                f"/Resources << /Font << /F1 {font_object} 0 R >> >> >>"
            ).encode("ascii")
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{number} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return bytes(out)


def pdf_bytes_from_html(html: str, *, title: str) -> bytes:
    """The fallback for an HTML-rendered export: its own sections, as text.

    Used when neither engine is installed, so the artifact still carries what the
    HTML bundle carried — the fallback cannot silently drop a section the HTML
    has, and the engines it could not use are named at the top of the page.
    """
    body = html_to_text(html)
    notes = pdf_engine_notes()
    if notes:
        unavailable = "; ".join(f"{engine} {reason}" for engine, reason in sorted(notes.items()))
        body = (
            "This dossier was rendered as text: no HTML-to-PDF engine is installed "
            f"({unavailable}). The sections below are the audit bundle's own.\n\n{body}"
        )
    return text_pdf(title, body)


def _pdf_via_simple(tree: dict[str, Any]) -> bytes:
    title = str(
        (tree.get("meta") or {}).get("title") or tree.get("document_id") or "Assure Document"
    )
    return text_pdf(title, jdf_to_markdown(tree))


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


def export_html_to_pdf(html: str, *, fallback_title: str = "Assure Document") -> bytes:
    """Render arbitrary HTML to PDF (certificate / audit bundle)."""
    pdf = _pdf_via_weasyprint(html)
    if pdf:
        return pdf
    pdf = _pdf_via_playwright(html)
    if pdf:
        return pdf
    return pdf_bytes_from_html(html, title=fallback_title)
