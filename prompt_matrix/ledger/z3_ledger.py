"""Z3 claim check with a human-readable explanation string.

This wraps the numeric truth ledger. It does not call an LLM. Phrase overlap
against ``context`` is a transparency hint, not semantic NLI.
"""

from __future__ import annotations

import re
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


def check_claim(
    claim: str,
    context: str = "",
    ledger: dict[str, Any] | None = None,
    *,
    source_label: str = "",
) -> dict[str, Any]:
    """Return confidence 0–1 plus a reason string for UI tooltips."""
    verified = run_z3_verification(claim, context=context, ledger=ledger)
    score = float(verified.get("score") or 0.5)
    pct, phrase = _overlap_pct(claim, context)
    label = (source_label or "").strip() or "source text"
    if phrase and pct >= 1:
        reason = (
            f"Matched {pct:.0f}% of source phrase {phrase!r} in {label}; "
            f"ledger status={verified.get('status')}."
        )
    elif context.strip():
        reason = f"No match found in {label}. Ledger status={verified.get('status')}."
    else:
        reason = f"Ledger status={verified.get('status')} (no source phrase provided)."
    return {
        "confidence": max(0.0, min(1.0, score)),
        "reason": reason,
        "status": verified.get("status"),
        "detail": verified.get("detail"),
        "score": score,
    }
