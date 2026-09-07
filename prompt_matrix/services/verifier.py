"""Claim verification — groundrails subprocess (optional) or native heuristic fallback."""

from __future__ import annotations

import logging
import re
from typing import Any

try:
    from ..services.lock_metadata import enrich_extracted_locks
    from .groundrails_subprocess import (
        cli_result_to_verdict,
        groundrails_python,
        groundrails_service_enabled,
        verify_claim_with_groundrails,
    )
    from .text_normalizer import normalize_text
except ImportError:
    from services.groundrails_subprocess import (
        cli_result_to_verdict,
        groundrails_python,
        groundrails_service_enabled,
        verify_claim_with_groundrails,
    )
    from services.lock_metadata import enrich_extracted_locks
    from services.text_normalizer import normalize_text

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

GROUNDRAILS_AVAILABLE = bool(groundrails_python())


def native_heuristic_verify(claim: str, source_text: str) -> dict[str, Any]:
    """Deterministic Python-native fallback when groundrails subprocess is unavailable."""
    words = [w for w in claim.lower().split() if len(w) > 3]
    matches = sum(1 for w in words if w in source_text.lower())
    match_ratio = (matches / len(words)) if words else 0.0
    is_grounded = match_ratio >= 0.4

    return {
        "claim": claim,
        "grounded": is_grounded,
        "score": round(match_ratio, 2),
        "support": {"passage": source_text[:100] if is_grounded else None, "offset": 0},
        "engine": "native-heuristic-fallback",
    }


class ClaimVerifier:
    def __init__(self, confidence_threshold: float = 0.85):
        self.confidence_threshold = confidence_threshold

    def verify_claims(self, claims: list[str], source_text: str) -> list[dict[str, Any]]:
        """Verify claims against normalized source text."""
        clean_source = normalize_text(source_text)
        results: list[dict[str, Any]] = []

        for claim in claims:
            results.append(self.verify_claim(claim, clean_source))

        return results

    def verify_claim(self, claim: str, source_text: str) -> dict[str, Any]:
        """Single-claim verification with optional groundrails subprocess."""
        if groundrails_service_enabled():
            cli_result = verify_claim_with_groundrails(claim, source_text)
            if cli_result is not None:
                return cli_result_to_verdict(claim, cli_result)

        return native_heuristic_verify(claim, source_text)


def _verdict_to_lock(
    verdict: dict[str, Any],
    *,
    source_id: str,
    web: bool,
) -> dict[str, Any] | None:
    if not verdict.get("grounded"):
        return None
    claim = str(verdict.get("claim") or "").strip()
    if not claim:
        return None
    support = verdict.get("support") or {}
    quoted = ""
    if isinstance(support, dict):
        quoted = str(support.get("passage") or "")
    engine = str(verdict.get("engine") or "groundrails")
    lock: dict[str, Any] = {
        "canonical_key": claim[:64],
        "metric": claim[:64],
        "value": 1,
        "confidence": float(verdict.get("score") or 0.85),
        "source_id": source_id,
        "page_coordinates": {"page": 1, "x": 0, "y": 0, "width": 100, "height": 24},
        "quoted": quoted,
        "verification_engine": engine,
    }
    if web:
        lock["web"] = True
        lock["pill"] = "🌐"
    return lock


def verify_claims(claims: list[str], sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Verify claims against sources; return lock metadata list."""
    evidence_docs: list[tuple[str, str]] = []
    for src in sources or []:
        sid = str(src.get("id") or "vault")
        name = str(src.get("name") or sid)
        text = str(src.get("excerpt") or src.get("extracted_text") or src.get("content") or "")
        if text.strip():
            evidence_docs.append((f"{name} ({sid})", text))

    if not evidence_docs:
        return []

    combined = "\n\n".join(text for _, text in evidence_docs)
    sources_used = [
        {
            "id": str(s.get("id") or ""),
            "name": str(s.get("name") or s.get("id") or ""),
        }
        for s in sources
    ]
    source_id = str((sources[0] if sources else {}).get("id") or "")
    web = bool(sources and sources[0].get("web"))
    if web:
        source_id = source_id or "web"

    verifier = ClaimVerifier()
    locks: list[dict[str, Any]] = []
    for verdict in verifier.verify_claims(claims, combined):
        lock = _verdict_to_lock(verdict, source_id=source_id, web=web)
        if lock:
            locks.append(lock)

    return enrich_extracted_locks(locks, sources_used)


def claims_from_text(text: str) -> list[str]:
    """Split streaming draft text into verifiable sentence claims."""
    chunks = _SENTENCE_SPLIT.split((text or "").strip())
    return [c.strip() for c in chunks if len(c.strip()) > 12]
