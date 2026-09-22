"""Parser routing: the one place that decides which parser backend parses a document.

``select_parser`` only decides the backend — it does not execute the parse,
handle direct-text ingestion, or own fallback behavior. Those stay in the
callers/ingest helpers, which execute the decision rather than make it:

- ``"jdf"``      → JDF CI (``services/jdf_converter.pdf_to_parse_bundle``)
- ``"textract"`` → AWS Textract (``lib/textract.TextractClient``)

Caller rule for a text-like file (extension in the text set, or
``source_kind="text"``): the ``"jdf"`` return here signals *wrap the text
content directly as a JDF document* — the caller skips binary parsing
entirely (no ``pdf_to_parse_bundle``, no Textract) and never probes the file
as PDF. That is execution of the routing decision, not a second router.
"""

from __future__ import annotations

from typing import Literal

ParserName = Literal["jdf", "textract"]

#: Extensions whose content is already text — the caller wraps it as a JDF
#: document directly, so no binary probing of any kind runs.
_TEXT_LIKE_EXTENSIONS = frozenset(
    {"txt", "md", "json", "csv", "rst", "yaml", "yml"}
)

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
        "textract" — use AWS Textract (``lib/textract.TextractClient``).

    ``source_kind`` (optional override):
        "scanned" → force Textract (the client knows the document is
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
        return "textract"
    if source_kind == "text":
        # Caller wraps text as JDF — skip binary parsing entirely.
        return "jdf"

    # 2. Filename hint for text-like files — skip binary probing entirely.
    if filename:
        ext = filename.lower().split(".")[-1]
        if ext in _TEXT_LIKE_EXTENSIONS:
            return "jdf"  # text-like: caller wraps content as JDF, no probe

    # 3. PDF probe (only for .pdf files).
    if filename and filename.lower().endswith(".pdf"):
        return _probe_pdf_for_parser(file_bytes)

    # 4. Default: try JDF CI.
    return "jdf"


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
            return "textract"  # no text found → likely scanned
    except Exception:
        return "jdf"  # probe failed → default to JDF, let parse fail naturally