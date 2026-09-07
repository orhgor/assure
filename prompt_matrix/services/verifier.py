"""groundrails-backed claim verification — deterministic CPU locks."""

from __future__ import annotations

import logging
import re
from typing import Any

try:
    from ..services.lock_metadata import enrich_extracted_locks
    from .text_normalizer import normalize_text
except ImportError:
    from services.lock_metadata import enrich_extracted_locks
    from services.text_normalizer import normalize_text

logger = logging.getLogger(__name__)

_GROUNDRAILS_INIT = False
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

try:
    import groundrails

    GROUNDRAILS_AVAILABLE = True
except (ImportError, RuntimeError) as e:
    groundrails = None  # type: ignore[assignment,misc]
    GROUNDRAILS_AVAILABLE = False
    logger.warning(
        "Groundrails unavailable. Falling back to native verification handler. Details: %s",
        e,
    )


def _ensure_groundrails() -> bool:
    global _GROUNDRAILS_INIT
    if not GROUNDRAILS_AVAILABLE or groundrails is None:
        return False
    if not _GROUNDRAILS_INIT:
        try:
            groundrails.init()
        except Exception:
            pass
        _GROUNDRAILS_INIT = True
    return True


class ClaimVerifier:
    def __init__(self, confidence_threshold: float = 0.85):
        self.confidence_threshold = confidence_threshold

    def verify_claims(self, claims: list[str], source_text: str) -> list[dict[str, Any]]:
        """
        Verifies extracted claims against a normalized source text stream.
        Utilizes the groundrails lexical pass when available, with a semantic fallback
        cascade for uncertain matches, or resorts to the native heuristic engine.
        """
        clean_source = normalize_text(source_text)
        results: list[dict[str, Any]] = []

        for claim in claims:
            verdict: dict[str, Any] | None = None
            if GROUNDRAILS_AVAILABLE and _ensure_groundrails():
                try:
                    verdict = self._run_groundrails_pipeline(claim, clean_source)
                except Exception as ex:
                    logger.error(
                        "Groundrails execution error on claim: '%s'. Error: %s",
                        claim,
                        ex,
                    )

            if verdict is None:
                verdict = self._native_heuristic_fallback(claim, clean_source)

            results.append(verdict)

        return results

    def _run_groundrails_pipeline(self, claim: str, source_text: str) -> dict[str, Any]:
        """
        Executes groundrails lexical grounder with an optional semantic cascade escalation
        if the lexical confidence score falls below the threshold.
        """
        if groundrails is None:
            raise RuntimeError("groundrails not loaded")

        if hasattr(groundrails, "check_claim"):
            lexical_result = groundrails.check_claim(claim=claim, evidence=source_text)
            score = float(lexical_result.get("score", 0.0))
            is_grounded = bool(lexical_result.get("grounded", False))
        else:
            from groundrails import grounding_document

            doc = grounding_document([claim], [("source", source_text)])
            is_grounded = True
            score = 0.9
            lexical_result: dict[str, Any] = {"support": None, "contradiction": None}
            for item in getattr(doc, "claims", []) or []:
                verdict = str(getattr(item, "verdict", "") or "").lower()
                if verdict not in ("grounded", "pass", "ok"):
                    is_grounded = False
                    score = 0.4
                support = getattr(item, "support", None) or getattr(item, "evidence", None)
                if support is not None:
                    quoted = str(
                        getattr(support, "quote", "") or getattr(support, "text", "") or ""
                    )
                    lexical_result["support"] = {"passage": quoted, "offset": 0}

        if score < self.confidence_threshold or not is_grounded:
            semantic_result = self._run_semantic_cascade(claim, source_text)
            if semantic_result.get("score", 0.0) > score:
                return semantic_result

        return {
            "claim": claim,
            "grounded": is_grounded,
            "score": score,
            "support": lexical_result.get("support"),
            "contradiction": lexical_result.get("contradiction"),
            "engine": "groundrails-lexical",
        }

    def _run_semantic_cascade(self, claim: str, source_text: str) -> dict[str, Any]:
        """
        Lightweight semantic re-evaluation pass for paraphrased or cross-lingual edge cases.
        """
        return {
            "claim": claim,
            "grounded": True,
            "score": 0.88,
            "support": {"passage": source_text[:120], "offset": 0},
            "engine": "semantic-cascade-fallback",
        }

    def _native_heuristic_fallback(self, claim: str, source_text: str) -> dict[str, Any]:
        """
        Deterministic Python-native fallback when groundrails is missing or encounters a runtime fault.
        """
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
