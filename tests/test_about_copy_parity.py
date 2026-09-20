"""Parity of the About copy: the dialog and the page must say the same thing.

Part A of ``prototype/about.html`` is duplicated, verbatim, as the
``#about-template`` block in ``prototype/index.html`` — the modal is cloned from
that template at runtime (``shell.js:_openAboutModal``), so the dialog is the
surface a reader actually meets when they click the ``?``, while ``about.html``
is the page they can link and print.

The two drifted: a session of corrections landed in the page and left the
dialog carrying a claim the page no longer made — "a document that drifts is
refused before you see it", on a build that had produced and rendered a document
whose every paragraph the verification model had rejected. Nothing detected it,
because nothing compared them. This module is that comparison, and it is the
reason the duplication is allowed to stand: the failure it prevents is silent,
and the fix — one copy — is not cheap here (the modal is built synchronously
from a ``<template>``; single-sourcing it means fetching and parsing a whole
page at dialog-open time, or adding a build step this static directory does not
have).

A claim about verification stated twice is one edit away from being two claims.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

PROTOTYPE = Path(__file__).resolve().parents[1] / "prototype"

#: Part A ends where the buyer's questions begin: the page carries Part B and C
#: past this heading, the dialog stops.
PART_A_START = "What Assure is"
PART_A_END = "What the buyer should ask"

#: ``<a class="about-more">`` is the dialog's link to the page and has no
#: counterpart in the page body, so it is the one element allowed to differ.
_SKIP = {"a"}


class _AboutBody(HTMLParser):
    """The ``h2``/``p`` items of a region, as ``("h2", "text")`` pairs.

    Both files are hand-written and shallow; a full tree is not needed to
    compare two lists of headings and paragraphs, and a parser keeps the
    comparison honest about nested markup (``<span class="about-cap">`` inside a
    paragraph is text, not structure).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, str]] = []
        self._open: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP:
            return
        if tag in ("h2", "p") and self._open is None:
            self._open = tag
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if self._open == tag:
            text = " ".join("".join(self._buf).split())
            self.items.append((tag, text))
            self._open = None
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._open is not None:
            self._buf.append(data)


def _part_a(html: str) -> list[tuple[str, str]]:
    """The Part A items of one document, in order."""
    parser = _AboutBody()
    parser.feed(html)
    out: list[tuple[str, str]] = []
    started = False
    for tag, text in parser.items:
        if not started:
            if tag == "h2" and text == PART_A_START:
                started = True
            else:
                continue
        if tag == "h2" and text == PART_A_END:
            break
        out.append((tag, text))
    assert started, f"Part A does not open on the heading {PART_A_START!r}"
    return out


def _index_template(html: str) -> str:
    """The ``<template id="about-template">`` block, with its markup."""
    start = html.index('<template id="about-template">')
    return html[start : html.index("</template>", start)]


def test_dialog_part_a_matches_the_page_verbatim():
    page = _part_a((PROTOTYPE / "about.html").read_text(encoding="utf-8"))
    modal = _part_a(_index_template((PROTOTYPE / "index.html").read_text(encoding="utf-8")))

    assert page, "Part A extracted no copy from about.html"
    for index in range(max(len(page), len(modal))):
        left = modal[index] if index < len(modal) else None
        right = page[index] if index < len(page) else None
        assert left == right, (
            f"the dialog and about.html disagree at Part A item {index}: "
            f"dialog={left!r} page={right!r}"
        )
