"""groundrails-backed claim verification — deterministic CPU locks."""

from __future__ import annotations

import re
from typing import Any

try:
    from ..services.lock_metadata import enrich_extracted_locks, lock_hash
except ImportError:
    from services.lock_metadata import enrich_extracted_locks, lock_hash

_GROUNDRAILS_INIT = False
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _ensure_groundrails() -> bool:
    global _GROUNDRAILS_INIT
    try:
        import groundrails

        if not _GROUNDRAILS_INIT:
            try:
                groundrails.init()
            except Exception:
                pass
            _GROUNDRAILS_INIT = True
        return True
    except ImportError:
        return False


def _fallback_verify(claim: str, evidence: str) -> tuple[bool, dict[str, Any]]:
    claim_tokens = {w.lower() for w in re.findall(r"[a-z0-9]+", claim) if len(w) > 3}
    if not claim_tokens:
        return True, {"engine": "fallback", "quoted": ""}
    source_lower = evidence.lower()
    missing = [t for t in claim_tokens if t not in source_lower]
    grounded = len(missing) <= max(1, len(claim_tokens) // 3)
    return grounded, {"engine": "fallback", "missing_tokens": missing}


def _groundrails_verify(
    claim: str, evidence_docs: list[tuple[str, str]]
) -> tuple[bool, dict[str, Any]]:
    try:
        from groundrails import grounding_document
    except ImportError:
        combined = "\n\n".join(text for _, text in evidence_docs)
        return _fallback_verify(claim, combined)

    try:
        doc = grounding_document([claim], evidence_docs)
    except Exception as exc:
        combined = "\n\n".join(text for _, text in evidence_docs)
        ok, meta = _fallback_verify(claim, combined)
        meta["engine"] = "groundrails_error"
        meta["error"] = str(exc)
        return ok, meta

    grounded = True
    coords: dict[str, Any] = {"page": 1, "x": 0, "y": 0, "width": 100, "height": 24}
    quoted = ""
    for item in getattr(doc, "claims", []) or []:
        verdict = str(getattr(item, "verdict", "") or "").lower()
        if verdict not in ("grounded", "pass", "ok"):
            grounded = False
        support = getattr(item, "support", None) or getattr(item, "evidence", None)
        if support is not None:
            quoted = str(getattr(support, "quote", "") or getattr(support, "text", "") or "")
            page = getattr(support, "page", None)
            if page is not None:
                coords["page"] = int(page)
            offset = getattr(support, "char_offset", None)
            if offset is not None:
                coords["x"] = int(offset)
    return grounded, {"engine": "groundrails", "quoted": quoted, "page_coordinates": coords}


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

    _ensure_groundrails()
    locks: list[dict[str, Any]] = []
    sources_used = [
        {
            "id": str(s.get("id") or ""),
            "name": str(s.get("name") or s.get("id") or ""),
        }
        for s in sources
    ]

    for claim in claims:
        text = (claim or "").strip()
        if not text:
            continue
        grounded, meta = _groundrails_verify(text, evidence_docs)
        if not grounded:
            continue
        source_id = str((sources[0] if sources else {}).get("id") or "")
        if sources and sources[0].get("web"):
            source_id = source_id or "web"
        lock: dict[str, Any] = {
            "canonical_key": text[:64],
            "metric": text[:64],
            "value": 1,
            "confidence": 0.85,
            "source_id": source_id,
            "page_coordinates": meta.get("page_coordinates")
            or {"page": 1, "x": 0, "y": 0, "width": 100, "height": 24},
            "quoted": meta.get("quoted") or "",
            "verification_engine": meta.get("engine") or "groundrails",
        }
        if sources and sources[0].get("web"):
            lock["web"] = True
            lock["pill"] = "🌐"
        locks.append(lock)

    return enrich_extracted_locks(locks, sources_used)


def claims_from_text(text: str) -> list[str]:
    """Split streaming draft text into verifiable sentence claims."""
    chunks = _SENTENCE_SPLIT.split((text or "").strip())
    return [c.strip() for c in chunks if len(c.strip()) > 12]
