"""Grounding verification wrapper (groundrails when available)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GroundingVerdict:
    grounded: bool
    unverified: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


def verify_claims(claims: str, evidence: str) -> GroundingVerdict:
    """Verify draft claims against vault evidence."""
    text = (claims or "").strip()
    source = (evidence or "").strip()
    if not text:
        return GroundingVerdict(grounded=True)
    if not source:
        return GroundingVerdict(grounded=False, unverified=[text])

    try:
        import groundrails
    except ImportError:
        # Lightweight fallback: require a shared token overlap for tests/dev.
        claim_tokens = {w.lower() for w in text.split() if len(w) > 3}
        source_lower = source.lower()
        missing = [t for t in claim_tokens if t not in source_lower]
        return GroundingVerdict(
            grounded=len(missing) <= max(1, len(claim_tokens) // 3),
            unverified=missing,
            details={"engine": "fallback"},
        )

    try:
        doc = groundrails.grounding_document([text], [("vault.md", source)])
    except Exception as exc:
        return GroundingVerdict(
            grounded=False, unverified=[str(exc)], details={"engine": "groundrails"}
        )

    unverified: list[str] = []
    grounded = True
    for item in getattr(doc, "claims", []) or []:
        verdict = str(getattr(item, "verdict", "") or "").lower()
        if verdict not in ("grounded", "pass", "ok"):
            grounded = False
            unverified.append(str(getattr(item, "text", "") or item))
    return GroundingVerdict(
        grounded=grounded, unverified=unverified, details={"engine": "groundrails"}
    )
