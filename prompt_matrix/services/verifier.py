"""Claim verification — groundrails subprocess (optional) or native fallback.

The native fallback is not a verifier (2026-09-27, ``docs/evidence-honesty.md``).
Until then it reported ``grounded: True`` with the reason "Source explicitly
carries the claim" whenever 40 % of the claim's words appeared anywhere in the
source, and quoted ``source_text[:100]`` — the file's first line — as the
supporting passage. Word overlap is not verification. The fallback now grounds
a claim only when the claim's sentence is found verbatim in the source (that
match is the passage, at its real offset); everything else is
``INSUFFICIENT_EVIDENCE`` with the reason stated.
"""

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
    from .llm_extraction import find_verbatim
    from .text_normalizer import normalize_text
except ImportError:
    from services.groundrails_subprocess import (
        cli_result_to_verdict,
        groundrails_python,
        groundrails_service_enabled,
        verify_claim_with_groundrails,
    )
    from services.llm_extraction import find_verbatim
    from services.lock_metadata import enrich_extracted_locks
    from services.text_normalizer import normalize_text

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

GROUNDRAILS_AVAILABLE = bool(groundrails_python())


INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
VERBATIM_MATCH = "VERBATIM_MATCH"
_HEURISTIC_REASON = "heuristic overlap is not verification"


def native_heuristic_verify(claim: str, source_text: str) -> dict[str, Any]:
    """Python-native fallback when the groundrails subprocess is unavailable.

    ``grounded`` is True only when the claim is found verbatim in the source
    (whitespace collapsed, case folded — ``llm_extraction.find_verbatim``);
    the passage is then the source's own text at that offset. Otherwise the
    verdict is ``INSUFFICIENT_EVIDENCE``: the word-overlap ratio is reported
    as ``overlap_ratio`` for transparency and is never a score.
    """
    text = (claim or "").strip()
    source = source_text or ""
    words = [w for w in text.lower().split() if len(w) > 3]
    matches = sum(1 for w in words if w in source.lower())
    overlap = (matches / len(words)) if words else 0.0
    hit = find_verbatim(source, text.rstrip(".!?")) if text else None
    if hit:
        return {
            "claim": claim,
            "grounded": True,
            "verdict": VERBATIM_MATCH,
            "reason": "claim found verbatim in the source",
            "score": None,
            "overlap_ratio": round(overlap, 2),
            "support": {"passage": source[hit[0] : hit[1]], "offset": hit[0]},
            "engine": "native-verbatim-fallback",
        }
    return {
        "claim": claim,
        "grounded": False,
        "verdict": INSUFFICIENT_EVIDENCE,
        "reason": _HEURISTIC_REASON,
        "score": None,
        "overlap_ratio": round(overlap, 2),
        "support": {"passage": None, "offset": None},
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
        # No confidence number: a verbatim match is a fact about the text, not a
        # probability, and the groundrails score is the engine's, reported as such.
        "confidence": None,
        "verdict": str(verdict.get("verdict") or ("grounded" if verdict.get("grounded") else INSUFFICIENT_EVIDENCE)),
        "reason": str(verdict.get("reason") or ""),
        "engine_score": verdict.get("score"),
        "source_id": source_id,
        # The page is unknown here: the sources are joined into one text with
        # no page layout, and no page is invented for it.
        "page_coordinates": None,
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
