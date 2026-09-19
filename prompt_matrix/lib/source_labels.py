"""Where a source row came from, carried in its label.

A vault row (``substrate_vault``) has no column for provenance — ``filename`` is
its only free text (see ``db/connection.py``; the schema is not this module's to
change). So a page the retrieval path fetched is labelled

    Fetched: mass.gov (retrieved 2026-09-18)

and the tag ``fetched_url: <host>`` is that label, read back by
``fetched_url_of``. Every surface that needs to tell a fetched page from an
uploaded file — the counter split, the SOURCES pane, the audit trail — parses
the label through this one pair of functions, so the tag has exactly one
definition.
"""

from __future__ import annotations

import re

FETCH_LABEL_PREFIX = "Fetched: "

_FETCH_LABEL_RE = re.compile(
    r"^Fetched:\s*(?P<host>[A-Za-z0-9.-]+)\s*\(retrieved\s+(?P<date>\d{4}-\d{2}-\d{2})\)$"
)


def fetched_label(host: str, retrieved_on: str) -> str:
    """The label a fetched page's vault row carries: ``Fetched: <host> (retrieved <date>)``."""
    clean_host = str(host or "").strip().lower().strip(".")
    date = str(retrieved_on or "").strip()
    if not clean_host or not date:
        raise ValueError("fetched_label needs a host and a retrieval date")
    return f"{FETCH_LABEL_PREFIX}{clean_host} (retrieved {date})"


def fetched_url_of(label: str) -> str:
    """``fetched_url`` for a source row's label — "" when it was not fetched."""
    match = _FETCH_LABEL_RE.match(str(label or "").strip())
    return match.group("host").lower() if match else ""


def retrieved_on_of(label: str) -> str:
    """The date in the fetched label — "" when the label is not a fetched one."""
    match = _FETCH_LABEL_RE.match(str(label or "").strip())
    return match.group("date") if match else ""
