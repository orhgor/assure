"""Red-Hat findings may quote the source only where the source says it.

Both Red-Hat producers (``routers/draft.run_redhat_audit`` for one node,
``tasks/redhat.py`` for the multipass draft audit) hand the model the source
sentences anchored to a node and get prose or JSON back. A model that is asked
to "check against the source" will, some of the time, quote a sentence that is
not in the source it was given — a paraphrase, a blend of two sentences, or text
from the draft presented as the source (observed in the 2026-09-27 review of
the compile stream's Red-Hat frames). A finding built on such a quote reaches
the reader as if the source had been checked and found wanting.

The rule mirrors ``services/redhat_graph.unsupported_claim_check`` (2026-09-26):
a finding that quotes the source is kept only when its quote is re-found
verbatim — under ``llm_extraction.find_verbatim``'s comparison, whitespace
collapsed and case folded — in the source text the audit was given. Anything
else is dropped, and the drop is written down; nothing is rewritten, because a
"corrected" quote would be a quote the model never made.

A finding that quotes nothing is not a grounding claim and passes with
``quote: None, quote_verbatim: False, evidence_kind: "observation"``: the
reader can see it did not cite. ``evidence_kind: "quoted"`` is set only when a
verbatim quote exists — every finding says what it stands on, and an
observation is never rendered as evidence (live run 2026-09-27: 3 of 18
findings quoted; the other 15 must not read as if they did).
"""

from __future__ import annotations

import re
from typing import Any

try:
    from .llm_extraction import find_verbatim
except ImportError:
    from services.llm_extraction import find_verbatim

EVIDENCE_QUOTED = "quoted"
EVIDENCE_OBSERVATION = "observation"

#: Quote marks a finding may wrap a source quotation in. Curly and guillemet
#: pairs are matched as pairs; straight double quotes are matched as a pair of
#: the same character. Apostrophes are not treated as quotes: too many
#: contractions in ordinary critique prose.
_QUOTED_SPAN = re.compile(r"“([^”]{8,400})”|«([^»]{8,400})»|\"([^\"]{8,400})\"")

#: Keys a finding may carry a source quotation under. ``highlight`` /
#: ``highlight_text`` are deliberately absent: the multipass schema uses them
#: for the *draft* text to highlight, which is not a claim about the source.
_QUOTE_KEYS = ("quote", "source_quote")


def quoted_spans(text: str) -> list[str]:
    """Every quotation the finding text wraps in quote marks, in order."""
    out: list[str] = []
    for match in _QUOTED_SPAN.finditer(text or ""):
        span = next((g for g in match.groups() if g), "").strip()
        if span and span not in out:
            out.append(span)
    return out


def finding_quotes(finding: dict[str, Any]) -> list[str]:
    """The strings a finding presents as source text: its ``quote`` field(s)
    and any quoted span in its ``content``."""
    quotes: list[str] = []
    for key in _QUOTE_KEYS:
        value = finding.get(key)
        if isinstance(value, str) and value.strip():
            quotes.append(value.strip())
        elif isinstance(value, list):
            quotes.extend(str(v).strip() for v in value if str(v or "").strip())
    for span in quoted_spans(str(finding.get("content") or "")):
        if span not in quotes:
            quotes.append(span)
    return quotes


def locate_quote(quote: str, source_texts: list[str]) -> str | None:
    """The source text as written, for a quote found verbatim in one of
    ``source_texts``; None when no source carries it."""
    for text in source_texts:
        if not text:
            continue
        hit = find_verbatim(text, quote)
        if hit:
            return text[hit[0] : hit[1]]
    return None


def verbatim_gate(
    findings: list[dict[str, Any]],
    source_texts: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep the findings whose source quotes are verbatim; drop the rest.

    Returns ``(kept, notes)``. Every kept finding carries ``quote`` (the source
    text as the source writes it, or None when the finding quoted nothing) and
    ``quote_verbatim`` (True only when a quote was re-found). A finding with a
    quote that no source text carries is dropped and named in ``notes`` — a
    quote the source does not contain is not evidence about the source, and
    a finding built on it must not reach the document.
    """
    kept: list[dict[str, Any]] = []
    notes: list[str] = []
    sources = [str(t or "") for t in source_texts if str(t or "").strip()]
    observations = 0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        quotes = finding_quotes(finding)
        if not quotes:
            out = dict(finding)
            out["quote"] = None
            out["quote_verbatim"] = False
            out["evidence_kind"] = EVIDENCE_OBSERVATION
            observations += 1
            kept.append(out)
            continue
        located = [locate_quote(q, sources) for q in quotes]
        missing = [q for q, hit in zip(quotes, located) if hit is None]
        if missing:
            title = str(finding.get("title") or "Red-Hat finding").strip()
            notes.append(
                f"dropped {title!r}: quoted text not found verbatim in the source "
                f"({missing[0][:80]!r})"
            )
            continue
        out = dict(finding)
        out["quote"] = located[0]
        if len(located) > 1:
            out["quotes"] = located
        out["quote_verbatim"] = True
        out["evidence_kind"] = EVIDENCE_QUOTED
        kept.append(out)
    if observations:
        notes.append(
            f"{observations} finding(s) returned no quote and are recorded as observations, "
            "not evidence"
        )
    return kept, notes


def with_evidence_kind(finding: dict[str, Any]) -> dict[str, Any]:
    """A finding written before ``evidence_kind`` existed (Red-Hat cache rows,
    stored telemetry) gets the label its own fields support: ``quoted`` only
    when it carries a verbatim quote."""
    out = dict(finding)
    if out.get("evidence_kind") not in (EVIDENCE_QUOTED, EVIDENCE_OBSERVATION):
        out["evidence_kind"] = (
            EVIDENCE_QUOTED
            if out.get("quote_verbatim") and str(out.get("quote") or "").strip()
            else EVIDENCE_OBSERVATION
        )
    return out


def anchored_quotes(node: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Every provenance row of ``node`` that carries a source sentence, with the
    window the matcher used beside it: ``{"quote", "window", "source_name",
    "page"}``. All of them, not the first — a paragraph anchored to three
    sentences is supported by the three together, and an audit handed one of
    them judges against less than the compile did."""
    out: list[dict[str, Any]] = []
    for row in (node or {}).get("provenance") or []:
        if not isinstance(row, dict):
            continue
        quote = str(row.get("extracted_quote") or "").strip()
        if not quote:
            continue
        page = row.get("page_number")
        if page in (None, ""):
            page = row.get("page")
        out.append(
            {
                "quote": quote,
                "window": str(row.get("anchor_window") or "").strip(),
                "source_name": str(row.get("source_name") or "").strip(),
                "page": page if page not in (None, "") else None,
            }
        )
    return out


def anchored_source_texts(rows: list[dict[str, Any]]) -> list[str]:
    """The texts a node-scoped audit may quote from: each anchored sentence and
    the window it was cut from."""
    texts: list[str] = []
    for row in rows:
        for key in ("window", "quote"):
            text = str(row.get(key) or "").strip()
            if text and text not in texts:
                texts.append(text)
    return texts


def format_source_block(rows: list[dict[str, Any]]) -> str:
    """The anchored quotes as the prompt lists them, one per line with origin."""
    lines: list[str] = []
    for index, row in enumerate(rows, start=1):
        origin = row.get("source_name") or "substrate"
        page = row.get("page")
        ref = f"{origin} p.{page}" if page not in (None, "") else str(origin)
        lines.append(f"[{index}] ({ref}) {row['quote']}")
    return "\n".join(lines)


__all__ = [
    "EVIDENCE_OBSERVATION",
    "EVIDENCE_QUOTED",
    "anchored_quotes",
    "anchored_source_texts",
    "finding_quotes",
    "format_source_block",
    "locate_quote",
    "quoted_spans",
    "verbatim_gate",
    "with_evidence_kind",
]
