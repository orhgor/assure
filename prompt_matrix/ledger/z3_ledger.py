"""Z3 claim check with a human-readable explanation string.

This wraps the numeric truth ledger. It does not call an LLM. Phrase overlap
against ``context`` is a transparency hint, not semantic NLI.

Evidence rules (2026-09-27, ``docs/evidence-honesty.md``): the ``excerpt`` is a
sentence of ``context`` located by search (the claim verbatim, or the source
sentence carrying the claim's figures) or ``None``; ``page_number`` is the page
that excerpt sits under (``--- Page N ---`` markers) or ``None``; there is no
``confidence`` number — ``numeric_consistency`` is one of ``matches_lock`` /
``no_lock`` / ``contradicts_lock``; ``verified_at`` is set only when a figure
was actually checked against a lock.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

try:
    from .truth_engine import NUMERIC_NO_LOCK, claim_numbers, run_z3_verification
except ImportError:
    from truth_engine import NUMERIC_NO_LOCK, claim_numbers, run_z3_verification

_WORD = re.compile(r"[A-Za-z0-9_%$.]+")
_PAGE_MARKER = re.compile(r"--- Page (\d+) ---")
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?", re.S)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _overlap_pct(claim: str, context: str) -> tuple[float, str]:
    claim_tokens = [t.lower() for t in _WORD.findall(claim or "") if len(t) > 1]
    ctx = context or ""
    if not claim_tokens or not ctx.strip():
        return 0.0, ""
    hits = [t for t in claim_tokens if t in ctx.lower()]
    pct = 100.0 * len(hits) / len(claim_tokens)
    phrase = " ".join(hits[:8]) if hits else ""
    return pct, phrase


def _page_at_offset(context: str, offset: int) -> int | None:
    """The page whose ``--- Page N ---`` marker last precedes ``offset``.

    Replaces ``_page_from_context`` (removed 2026-09-27), which returned the
    first page containing *any* word of the overlap phrase — a page for the
    word "the", presented as the page the claim was found on.
    """
    page: int | None = None
    for match in _PAGE_MARKER.finditer(context or ""):
        if match.start() > offset:
            break
        page = int(match.group(1))
    return page


def locate_excerpt(claim: str, context: str) -> tuple[str | None, int | None]:
    """``(excerpt, page)``: the claim found verbatim in ``context`` (whitespace
    collapsed, case folded), else the first context sentence that carries every
    figure the claim states, else ``(None, None)``.

    Nothing else counts: the previous fallback returned ``context[:240]`` and
    then the claim's own text, which presented the claim as its own source.
    """
    ctx = context or ""
    if not ctx.strip() or not (claim or "").strip():
        return None, None
    stripped = _PAGE_MARKER.sub(lambda m: " " * len(m.group(0)), ctx)
    words = (claim or "").split()
    if words:
        pattern = r"\s+".join(re.escape(w) for w in words)
        match = re.search(pattern, stripped, flags=re.IGNORECASE)
        if match:
            return match.group(0).strip(), _page_at_offset(ctx, match.start())
    numbers = _NUMBER.findall(claim or "")
    if numbers:
        for match in _SENTENCE.finditer(stripped):
            sentence = match.group(0)
            if not sentence.strip():
                continue
            if all(n in sentence for n in numbers):
                return sentence.strip(), _page_at_offset(ctx, match.start())
    return None, None


def _rule_from_ledger(claim: str, ledger: dict[str, Any] | None) -> str | None:
    """The lock the claim was checked against, as ``key == value``; None when
    the claim names no lock (the old ``"ledger_check"`` label named a check
    that did not happen)."""
    numbers = claim_numbers(claim)
    for key, raw in (ledger or {}).items():
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if numbers and any(abs(val - n) < 1e-9 for n in numbers):
            return f"{key} == {val}"
        if str(key).lower() in (claim or "").lower():
            return f"{key} == {val}"
    return None


def check_claim(
    claim: str,
    context: str = "",
    ledger: dict[str, Any] | None = None,
    *,
    source_label: str = "",
    source_id: str = "",
) -> dict[str, Any]:
    """Numeric consistency plus a reason string for UI tooltips.

    ``confidence`` and ``score`` are always ``None`` (kept as keys so readers
    find an explicit null); ``numeric_consistency`` carries the verdict word.
    """
    verified = run_z3_verification(claim, context=context, ledger=ledger)
    consistency = str(verified.get("numeric_consistency") or NUMERIC_NO_LOCK)
    pct, phrase = _overlap_pct(claim, context)
    label = (source_label or "").strip() or "source text"
    excerpt, page = locate_excerpt(claim, context)
    page_suffix = f" on page {page}" if page else ""
    if phrase and pct >= 1:
        reason = (
            f"Matched {pct:.0f}% of source phrase {phrase!r} in {label}{page_suffix}; "
            f"numeric consistency: {consistency}."
        )
    elif context.strip():
        reason = f"No match found in {label}. Numeric consistency: {consistency}."
    else:
        reason = f"Numeric consistency: {consistency} (no source phrase provided)."
    rule = _rule_from_ledger(claim, ledger) if consistency != NUMERIC_NO_LOCK else None
    checked = consistency != NUMERIC_NO_LOCK
    provenance = {
        "source_id": (source_id or "").strip(),
        "source_name": label,
        "page_number": page,
        "excerpt": excerpt,
        "rule": rule,
        "confidence": None,
        "numeric_consistency": consistency,
        "verified_at": datetime.now(UTC).isoformat() if checked else None,
    }
    return {
        "confidence": None,
        "numeric_consistency": consistency,
        "reason": reason,
        "status": verified.get("status"),
        "detail": verified.get("detail"),
        "score": None,
        "provenance": provenance,
    }
