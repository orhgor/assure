"""Z3 claim check with a human-readable explanation string.

This wraps the numeric truth ledger. It does not call an LLM. Phrase overlap
against ``context`` is a transparency hint, not semantic NLI.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

try:
    from .truth_engine import run_z3_verification
except ImportError:
    from truth_engine import run_z3_verification

_WORD = re.compile(r"[A-Za-z0-9_%$.]+")


def _overlap_pct(claim: str, context: str) -> tuple[float, str]:
    claim_tokens = [t.lower() for t in _WORD.findall(claim or "") if len(t) > 1]
    ctx = context or ""
    if not claim_tokens or not ctx.strip():
        return 0.0, ""
    hits = [t for t in claim_tokens if t in ctx.lower()]
    pct = 100.0 * len(hits) / len(claim_tokens)
    phrase = " ".join(hits[:8]) if hits else ""
    return pct, phrase


def _page_from_context(context: str, phrase: str) -> int | None:
    """Return page number if context uses --- Page N --- markers."""
    if not context.strip():
        return None
    sections = re.split(r"--- Page (\d+) ---", context)
    if len(sections) <= 1:
        return None
    needle = (phrase or "").strip().lower()
    if not needle:
        return None
    # sections alternate: [pre, page_num, text, page_num, text, ...]
    for i in range(1, len(sections) - 1, 2):
        page_num = int(sections[i])
        body = (sections[i + 1] or "").lower()
        if needle in body or any(w in body for w in needle.split() if len(w) > 2):
            return page_num
    return None


def _rule_from_ledger(claim: str, ledger: dict[str, Any] | None) -> str:
    numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", claim or "")]
    for key, raw in (ledger or {}).items():
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if numbers and any(abs(val - n) < 1e-9 for n in numbers):
            return f"{key} == {val}"
        if str(key).lower() in (claim or "").lower():
            return f"{key} == {val}"
    if ledger:
        key, raw = next(iter(ledger.items()))
        return f"{key} == {raw}"
    return "ledger_check"


def _excerpt_from_context(context: str, phrase: str) -> str:
    ctx = (context or "").strip()
    if phrase and phrase in ctx:
        idx = ctx.lower().find(phrase.lower())
        start = max(0, idx - 60)
        end = min(len(ctx), idx + len(phrase) + 60)
        return ctx[start:end].strip()
    return ctx[:240].strip() if ctx else ""


def check_claim(
    claim: str,
    context: str = "",
    ledger: dict[str, Any] | None = None,
    *,
    source_label: str = "",
    source_id: str = "",
) -> dict[str, Any]:
    """Return confidence 0–1 plus a reason string for UI tooltips."""
    verified = run_z3_verification(claim, context=context, ledger=ledger)
    score = float(verified.get("score") or 0.5)
    pct, phrase = _overlap_pct(claim, context)
    label = (source_label or "").strip() or "source text"
    page = _page_from_context(context, phrase)
    page_suffix = f" on page {page}" if page else ""
    if phrase and pct >= 1:
        reason = (
            f"Matched {pct:.0f}% of source phrase {phrase!r} in {label}{page_suffix}; "
            f"ledger status={verified.get('status')}."
        )
    elif context.strip():
        reason = f"No match found in {label}. Ledger status={verified.get('status')}."
    else:
        reason = f"Ledger status={verified.get('status')} (no source phrase provided)."
    rule = _rule_from_ledger(claim, ledger)
    excerpt = _excerpt_from_context(context, phrase) or (claim or "")[:240]
    provenance = {
        "source_id": (source_id or "").strip(),
        "source_name": label,
        "page_number": page,
        "excerpt": excerpt,
        "rule": rule,
        "confidence": max(0.0, min(1.0, score)),
        "verified_at": datetime.now(UTC).isoformat(),
    }
    return {
        "confidence": max(0.0, min(1.0, score)),
        "reason": reason,
        "status": verified.get("status"),
        "detail": verified.get("detail"),
        "score": score,
        "provenance": provenance,
    }
