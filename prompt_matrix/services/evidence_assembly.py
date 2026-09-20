"""Deterministic evidence assembly and verdict classification for Assure.

This module consolidates evidence collection, candidate anchor selection,
verdict classification, and provenance preservation into a single
deterministic function.

Key design principles:
- Lexical overlap is ONLY used for candidate anchor selection, NEVER as final verdict
- Verdicts follow strict taxonomy: supported, partial, not_supported, contradicted, unanchored, unverified
- contradicted requires EXPLICIT opposition from source
- not_supported = source is silent; contradicted = source says opposite
- Provenance is preserved for every evidence item
- Output is fully deterministic for same logical inputs
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

EVIDENCE_TEMPLATE_VERSION = "1.0.0"
PIPELINE_VERSION = 1

EVIDENCE_COMPILE_TYPES = ("full", "selection")

# Budget limits
EVIDENCE_PER_FILE_CHARS = 4000
EVIDENCE_TOTAL_CHARS = 16000

# Minimum claim tokens for eligibility
MIN_CLAIM_TOKENS = 4
MIN_ANCHOR_OVERLAP = 4
MIN_ANCHOR_COEFFICIENT = 0.60
MAX_ANCHOR_WINDOW = 3

# ──────────────────────────────────────────────────────────────────────────────
# Data Classes
# ──────────────────────────────────────────────────────────────────────────────

Verdict = Literal[
    "supported",
    "partial",
    "not_supported",
    "contradicted",
    "unanchored",
    "unverified",
]

TrustLabel = Literal["trusted", "untrusted"]
EvidenceCompileType = Literal["full", "selection"]


@dataclass(frozen=True)
class EvidenceBudget:
    """Source budget limits for evidence assembly."""
    per_file_chars: int = 4000
    total_chars: int = 16000


@dataclass(frozen=True)
class EvidenceExcerpt:
    """A single source excerpt with provenance and trust metadata."""
    file_id: str
    filename: str
    excerpt: str
    page: str | None = None
    section: str | None = None
    row: str | None = None
    trust: Literal["trusted", "untrusted"] = "trusted"
    truncated: bool = False
    original_length: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "filename": self.filename,
            "excerpt": self.excerpt,
            "page": self.page,
            "section": self.section,
            "row": self.row,
            "trust": self.trust,
            "truncated": self.truncated,
            "original_length": self.original_length,
        }


@dataclass(frozen=True)
class EvidenceAnchor:
    """A candidate anchor from source material."""
    file_id: str
    filename: str
    excerpt: str
    page: str | None = None
    section: str | None = None
    row: str | None = None
    trust: Literal["trusted", "untrusted"] = "trusted"
    truncated: bool = False
    original_length: int = 0
    overlap_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "filename": self.filename,
            "excerpt": self.excerpt,
            "page": self.page,
            "section": self.section,
            "row": self.row,
            "trust": self.trust,
            "truncated": self.truncated,
            "original_length": self.original_length,
            "overlap_score": self.overlap_score,
        }


@dataclass(frozen=True)
class EvidenceVerdict:
    """Verdict for a single claim against evidence."""
    verdict: Literal["supported", "partial", "not_supported", "contradicted", "unanchored", "unverified"]
    reason: str
    anchor: dict[str, Any] | None = None
    quotes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "anchor": self.anchor,
            "quotes": list(self.quotes),
        }


@dataclass(frozen=True)
class EvidenceAssemblyResult:
    """Complete result of evidence assembly for a single claim."""
    claim: str
    verdict: EvidenceVerdict
    anchors: tuple[dict[str, Any], ...]
    quotes: tuple[str, ...]
    provenance: tuple[dict[str, Any], ...]
    truncation: dict[str, Any] | None
    fingerprint: str
    compile_type: Literal["full", "selection"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "verdict": self.verdict.to_dict(),
            "anchors": list(self.anchors),
            "quotes": list(self.quotes),
            "provenance": list(self.provenance),
            "truncation": self.truncation,
            "fingerprint": self.fingerprint,
            "compile_type": self.compile_type,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────

class EvidenceAssemblyError(ValueError):
    """Base exception for evidence assembly errors."""
    pass


class MissingRequiredInputError(EvidenceAssemblyError):
    """Raised when a required input is missing."""
    pass


class InvalidCompileTypeError(EvidenceAssemblyError):
    """Raised when compile_type is invalid."""
    pass


class RequiredSourceUnavailableError(EvidenceAssemblyError):
    """Raised when required source context is unavailable."""
    pass


# ──────────────────────────────────────────────────────────────────────────────
# Normalization
# ──────────────────────────────────────────────────────────────────────────────

_WHITESPACE_RE = re.compile(r"\s+")

def _normalize_whitespace(text: str) -> str:
    """Normalize whitespace: collapse runs, strip ends."""
    if not text:
        return ""
    return _WHITESPACE_RE.sub(" ", text.strip())


def _normalize_compile_type(value: str) -> Literal["full", "selection"]:
    """Normalize and validate compile type."""
    normalized = value.strip().lower()
    if normalized not in EVIDENCE_COMPILE_TYPES:
        raise InvalidCompileTypeError(
            f"Invalid compile_type: {value!r}. Must be one of {EVIDENCE_COMPILE_TYPES}"
        )
    return normalized  # type: ignore[return-value]
def _overlap_pct(claim: str, context: str) -> tuple[float, str]:
    """Calculate token overlap percentage and matching phrase."""
    claim_tokens = [t.lower() for t in _WORD_RE.findall(claim or "") if len(t) > 1]
    ctx_tokens = [t.lower() for t in _WORD_RE.findall(context or "") if len(t) > 1]
    if not claim_tokens or not context.strip():
        return 0.0, ""
    
    # Core claim tokens: numbers and domain-specific terms (length > 3)
    claim_core = {t for t in claim_tokens if t.isdigit() or (not t.isdigit() and len(t) > 3)}
    # Common words to downweight
    stopwords = {"in", "the", "and", "of", "to", "for", "with", "on", "at", "by", "from", "as", "is", "was", "were", "be", "been", "have", "has", "had", "will", "would", "could", "should", "may", "might", "must", "shall", "can", "do", "did", "does", "am", "are", "or", "but", "not", "a", "an", "the", "this", "that", "these", "those", "it", "its", "their", "our", "your", "my", "his", "her", "him", "she", "he", "we", "they", "us", "them"}
    claim_core = claim_core - stopwords
    
    # Normalize numbers for matching (strip suffixes like M, B, K, %)
    def normalize_number(token: str) -> str:
        # Remove currency/percentage suffixes
        token = token.rstrip('MBKmbk%')
        return token
    
    # Normalize claim core numbers
    claim_core_norm = {normalize_number(t) for t in claim_core}
    
    # Find hits in source
    ctx_tokens = [t.lower() for t in _WORD_RE.findall(context or "") if len(t) > 1]
    ctx_set = set(ctx_tokens)
    ctx_norm = {normalize_number(t) for t in ctx_tokens}
    
    # Core claim terms
    hits = [t for t in claim_core if t in ctx_set]
    
    # Also match on normalized numbers
    for t in claim_core:
        if t.isdigit() or (not t.isdigit() and len(t) > 3):
            norm_t = normalize_number(t)
            if norm_t in ctx_norm:
                hits.append(t)
    
    # Remove duplicates
    hits = list(set(hits))
    
    # Check for exact phrase matches (boost score)
    exact_phrase_bonus = 0.0
    claim_lower = " ".join([t for t in _WORD_RE.findall(claim or "") if len(t) > 1]).lower()
    context_lower = " ".join([t for t in _WORD_RE.findall(context or "") if len(t) > 1]).lower()
    if claim_lower in context_lower:
        exact_phrase_bonus = 20.0
    
    # Check for number consistency
    number_bonus = 0.0
    claim_numbers = re.findall(r"-?\d+(?:,\d+)*(?:\.\d+)?", claim)
    context_numbers = re.findall(r"-?\d+(?:,\d+)*(?:\.\d+)?", context)
    if claim_numbers and set(claim_numbers) & set(context_numbers):
        number_bonus = 10.0
    
    pct = 100.0 * len(hits) / len(claim_core) if claim_core else 0.0
    # Boost for exact phrase and number matches
    pct = min(100.0, pct + exact_phrase_bonus + number_bonus)
    phrase = " ".join(hits[:8]) if hits else ""
    return pct, phrase

def _classify_source(text: str) -> Literal["trusted", "untrusted"]:
    """Classify source text as trusted or untrusted based on instruction-like content."""
    instruction_patterns = [
        r"ignore previous instructions",
        r"do not disclose",
        r"you are now",
        r"act as",
        r"pretend to be",
        r"system prompt",
        r"ignore.*instruction",
        r"disregard.*instruction",
        r"forget.*instruction",
        r"override.*instruction",
    ]
    text_lower = text.lower()
    for pattern in instruction_patterns:
        if re.search(pattern, text_lower):
            return "untrusted"
    return "trusted"
# ──────────────────────────────────────────────────────────────────────────────

def _rank_source_rows(
    source_rows: list[dict[str, Any]], icp_profile: str | None
) -> list[dict[str, Any]]:
    """Order sources so the claim-relevant ones are the ones the budget keeps.

    The budget below is far tighter than the compile prompt's (4k per file,
    16k total against 200k/400k), and it stops at the first source that does not
    fit, so ordering decides which evidence is assembled at all. Ranking by
    confidence first and ICP vocabulary second keeps a poorly parsed but
    vocabulary-dense source from displacing a clean one; ties stay in document
    order, so the same rows always assemble the same way.

    The key matches routers/draft._rank_substrate_rows deliberately: the
    evidence a verdict is drawn from and the sources the draft was written from
    should rank the same material first, or a compile can cite a source the
    evidence pass never read.
    """
    try:
        from .icp_profiles import icp_keyword_boost
    except ImportError:
        from icp_profiles import icp_keyword_boost

    def _confidence(row: dict[str, Any]) -> float:
        for key in ("parse_confidence", "confidence"):
            raw = row.get(key)
            if isinstance(raw, (int, float)):
                return float(raw)
        return 0.0

    def _key(item: tuple[int, dict[str, Any]]):
        idx, row = item
        text = str(row.get("extracted_text") or "")
        return (-_confidence(row), -icp_keyword_boost(text, icp_profile), idx)

    return [row for _idx, row in sorted(enumerate(source_rows), key=_key)]


def _enforce_source_budget(
    source_rows: list[dict[str, Any]],
    budget: EvidenceBudget,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Enforce per-file and total source budgets, return excerpts and truncation summary."""
    
    excerpts: list[dict[str, Any]] = []
    total_chars = 0
    truncated_files: list[str] = []
    
    for row in source_rows:
        text = str(row.get("extracted_text") or "").strip()
        if not text:
            continue
        
        original_length = len(text)
        trust = _classify_source(text)
        
        # Per-file cap
        truncated = False
        if len(text) > EVIDENCE_PER_FILE_CHARS:
            text = text[:EVIDENCE_PER_FILE_CHARS]
            truncated = True
        
        # Total budget
        if total_chars + len(text) > EVIDENCE_TOTAL_CHARS:
            remaining = EVIDENCE_TOTAL_CHARS - total_chars
            if remaining > 0:
                text = text[:remaining]
                truncated = True
            else:
                break
        
        total_chars += len(text)
        
        excerpt = {
            "file_id": str(row.get("id") or ""),
            "filename": str(row.get("filename") or "substrate"),
            "excerpt": text,
            "page": str(row.get("page")) if row.get("page") else None,
            "section": str(row.get("section")) if row.get("section") else None,
            "row": str(row.get("row")) if row.get("row") else None,
            "trust": trust,
            "truncated": truncated,
            "original_length": original_length,
        }
        excerpts.append(excerpt)
        
        if truncated:
            truncated_files.append(excerpt["file_id"])
        
        if total_chars >= EVIDENCE_TOTAL_CHARS:
            break
    
    truncation_summary = {
        "per_file_cap": EVIDENCE_PER_FILE_CHARS,
        "total_cap": EVIDENCE_TOTAL_CHARS,
        "total_chars_used": sum(len(e["excerpt"]) for e in excerpts),
        "truncated_files": truncated_files,
        "truncation_occurred": len(truncated_files) > 0,
    }
    
    return excerpts, truncation_summary


# ──────────────────────────────────────────────────────────────────────────────
# Candidate Anchor Selection
# ──────────────────────────────────────────────────────────────────────────────

_WORD_RE = re.compile(r"[A-Za-z0-9_%$.]+")

def _tokenize(text: str) -> set[str]:
    """Tokenize text into normalized tokens."""
    return {t.lower() for t in _WORD_RE.findall(text or "") if len(t) > 1}


def _numeric_tokens(text: str) -> set[str]:
    """The numbers a text states, normalized so "25,000" and "25000" are equal.

    Compared as written values rather than as floats: a policy's figures are
    identifiers as much as quantities, and "5,000,000" against "5000000" is the
    same figure stated two ways, not a conflict.
    """
    out: set[str] = set()
    for raw in re.findall(r"\d+(?:[,\d]*)(?:\.\d+)?", text or ""):
        cleaned = raw.replace(",", "").rstrip(".")
        if cleaned:
            out.add(cleaned)
    return out


def _weighted_match_ratio(claim: str, excerpt: str) -> float:
    """How much of the claim the excerpt carries, weighting the terms that decide it.

    A flat bag-of-words count over the whole excerpt cannot see the word that
    changes the answer. Measured: "The policy covers flood damage" against "The
    policy covers fire damage." scores 3 of 4 key words and cleared the 0.7
    support threshold, because "policy", "covers" and "damage" matched and
    "flood" carried no more weight than they did — yet flood-versus-fire is the
    entire question.

    Three terms are therefore weighted: a number, a negation, and a token the
    excerpt does not carry. The last is what lets a distinguishing noun count —
    an unmatched word is evidence about the claim's subject, not noise to be
    averaged away. A term that *is* matched keeps weight 1, so an ordinary
    verbatim claim still scores 1.0 and the threshold keeps its meaning.
    """
    claim_tokens = [t for t in _WORD_RE.findall(claim or "") if len(t) > 3]
    if not claim_tokens:
        return 0.0
    excerpt_words = {t.lower() for t in _WORD_RE.findall(excerpt or "")}
    excerpt_lower = (excerpt or "").lower()
    claim_numbers = _numeric_tokens(claim)
    excerpt_numbers = _numeric_tokens(excerpt)
    claim_negated = any(n in claim.lower() for n in _NEGATIONS)

    total = 0.0
    matched = 0.0
    for token in claim_tokens:
        token_l = token.lower()
        hit = token_l in excerpt_words
        weight = 1.0
        if token_l in _DISTINGUISHING or token_l in claim_numbers:
            weight = 2.0
        elif token_l in _NEGATIONS:
            weight = 2.0
        total += weight
        if hit:
            matched += weight
    # A claim that states a figure the excerpt states differently cannot be
    # supported by the words around the figure, however many of them match.
    if claim_numbers and excerpt_numbers and not (claim_numbers & excerpt_numbers):
        matched = min(matched, total * 0.5)
    if claim_negated != any(n in excerpt_lower for n in _NEGATIONS):
        matched = min(matched, total * 0.5)
    return matched / total if total else 0.0


#: Tokens that select the claim's subject rather than describe it. A miss on one
#: of these is the difference between the claim and its near-neighbour, so it is
#: weighted above a match on shared vocabulary.
_DISTINGUISHING = frozenset(
    {
        "flood", "fire", "water", "wind", "earthquake", "storm", "hail", "theft",
        "vandalism", "mold", "collapse", "flooding", "smoke", "explosion",
        "suffolk", "nassau", "county", "borough", "premises", "building",
        "deductible", "limit", "limits", "exclusion", "exclusions",
        "endorsement", "endorsements", "coverage", "excluded", "included",
        "replacement", "actual", "cash", "value", "insured", "mortgagee",
        "additional", "loss", "payee", "location", "property", "schedule",
    }
)

_NEGATIONS = (
    "not ", "no ", "never ", "n't ", "cannot ", "doesn't ", "does not ",
    "won't ", "will not ", "isn't ", "is not ", "without ",
)


def _find_candidate_anchors(
    claim: str,
    source_excerpts: list[dict[str, Any]],
    compile_type: Literal["full", "selection"],
    selected_node_ids: list[str] | None = None,
    budget: EvidenceBudget | None = None,
) -> list[dict[str, Any]]:
    """
    Find candidate anchor spans from source excerpts using lexical overlap.
    
    This is ONLY for candidate selection - NOT for final verdict.
    """
    if not source_excerpts:
        return []
    
    claim_tokens = [t for t in _WORD_RE.findall(claim or "") if len(t) > 1]
    if not claim_tokens:
        return []
    
    candidates = []
    
    for excerpt in source_excerpts:
        text = excerpt.get("excerpt", "")
        if not text:
            continue
        
        pct, phrase = _overlap_pct(claim, text)
        # Only filter out if overlap is 0% (no matching tokens at all)
        if pct <= 0.0:
            continue
        
        # Calculate overlap score
        overlap_score = pct / 100.0
        
        anchor = {
            "file_id": excerpt.get("file_id", ""),
            "filename": excerpt.get("filename", ""),
            "excerpt": excerpt.get("excerpt", ""),
            "page": excerpt.get("page"),
            "section": excerpt.get("section"),
            "row": excerpt.get("row"),
            "trust": excerpt.get("trust", "trusted"),
            "truncated": excerpt.get("truncated", False),
            "original_length": excerpt.get("original_length", 0),
            "overlap_score": overlap_score,
            "overlap_phrase": phrase,
            "overlap_pct": pct,
        }
        candidates.append(anchor)
    
    # If no candidates meet minimum threshold but we have source excerpts,
    # include the best one anyway so verdict logic can classify as not_supported
    if not candidates and source_excerpts:
        best_excerpt = max(source_excerpts, key=lambda e: _overlap_pct(claim, e.get("excerpt", ""))[0])
        pct, phrase = _overlap_pct(claim, best_excerpt.get("excerpt", ""))
        overlap_score = pct / 100.0
        anchor = {
            "file_id": best_excerpt.get("file_id", ""),
            "filename": best_excerpt.get("filename", ""),
            "excerpt": best_excerpt.get("excerpt", ""),
            "page": best_excerpt.get("page"),
            "section": best_excerpt.get("section"),
            "row": best_excerpt.get("row"),
            "trust": best_excerpt.get("trust", "trusted"),
            "truncated": best_excerpt.get("truncated", False),
            "original_length": best_excerpt.get("original_length", 0),
            "overlap_score": overlap_score,
            "overlap_phrase": phrase,
            "overlap_pct": pct,
        }
        candidates.append(anchor)
    
    # Sort by overlap score descending, then by filename for stability
    candidates.sort(key=lambda x: (-x["overlap_score"], x.get("filename", "")))
    
    # For selection compile, limit to top candidates
    if compile_type == "selection":
        candidates = candidates[:3]
    
    return candidates


# ──────────────────────────────────────────────────────────────────────────────
# Verdict Classification
# ──────────────────────────────────────────────────────────────────────────────

def _classify_verdict(
    claim: str,
    candidates: list[dict[str, Any]],
) -> tuple[Literal["supported", "partial", "not_supported", "contradicted", "unanchored", "unverified"], str, dict | None, tuple[str, ...]]:
    """
    Classify verdict based on candidate anchors.
    
    STRICT RULES:
    1. contradicted ONLY when source EXPLICITLY states the opposite
    2. Anchor selection (lexical overlap) NEVER sets verdict
    3. not_supported is DEFAULT when source is silent
    
    Rules:
    - supported: source explicitly carries the claim
    - partial: source supports main claim but misses qualifier/exception/scope
    - not_supported: source is silent on the claim
    - contradicted: source EXPLICITLY says the opposite (requires explicit opposition)
    - unanchored: no valid source span
    - unverified: not checked (shouldn't happen here)
    """
    if not candidates:
        return "unanchored", "No source span available for this claim.", None, ()
    
    # Sort by overlap score for candidate ranking
    sorted_candidates = sorted(candidates, key=lambda x: -x.get("overlap_score", 0))
    best = sorted_candidates[0]
    # Get the best candidate's excerpt
    best_excerpt = best.get("excerpt", "")
    if not best_excerpt:
        return "unanchored", "Candidate anchor has no excerpt.", None, ()
    
    claim_lower = claim.lower()
    excerpt_lower = best_excerpt.lower()
    
    # Extract key claim elements for semantic comparison
    claim_key_words = [w for w in claim_lower.split() if len(w) > 3]
    if not claim_key_words:
        return "unverified", "Claim has no meaningful keywords.", None, ()
    
    # Weighted rather than a flat count: see _weighted_match_ratio for the
    # measured case where an unweighted ratio called flood-vs-fire supported.
    match_ratio = _weighted_match_ratio(claim, best_excerpt)
    
    # Extract numbers for numeric verification
    claim_numbers = re.findall(r"-?\d+(?:,\d+)*(?:\.\d+)?", claim)
    excerpt_numbers = re.findall(r"-?\d+(?:,\d+)*(?:\.\d+)?", best_excerpt)
    number_matches = len(set(claim_numbers) & set(excerpt_numbers)) if claim_numbers else 0
    number_match_ratio = number_matches / len(claim_numbers) if claim_numbers else 1.0
    # STRICT RULE 1: Check for EXPLICIT contradiction first
    # Only contradicted if source EXPLICITLY states the opposite
    has_explicit_opposition = _has_explicit_opposition(claim, best_excerpt)
    
    # Check for explicit negation mismatch
    claim_has_negation = any(neg in claim_lower for neg in ["not ", "no ", "never ", "n't ", "cannot ", "doesn't ", "does not ", "won't ", "will not "])
    excerpt_has_negation = any(neg in excerpt_lower for neg in ["not ", "no ", "never ", "n't ", "cannot ", "doesn't ", "does not ", "won't ", "will not "])
    
    # STRICT RULE 1: Contradiction requires EXPLICIT opposition
    if _has_explicit_opposition(claim, best_excerpt):
        verdict = "contradicted"
        reason = "Source explicitly states the opposite of the claim."
        quotes = (_extract_opposing_quote(best_excerpt, claim),)
        return verdict, reason, candidates[0], quotes
    
    # STRICT RULE 2: Anchor overlap NEVER determines verdict
    # Only use match_ratio for semantic support assessment
    
    # STRICT RULE 3: not_supported is DEFAULT when source is silent
    # High semantic support → supported
    if match_ratio >= 0.7 and number_match_ratio >= 0.8:
        verdict = "supported"
        reason = "Source explicitly carries the claim."
        quotes = (_extract_supporting_quote(best_excerpt, claim),)
        return verdict, "Source explicitly carries the claim.", candidates[0], quotes
    
    # Partial support: some key terms match but qualifiers/numbers missing
    if match_ratio >= 0.4:
        verdict = "partial"
        reason = "Source supports part of the claim but misses qualifiers or details."
        quotes = (_extract_supporting_quote(best_excerpt, claim),)
        return verdict, reason, candidates[0], quotes
    
    # Low match ratio: check for explicit contradiction first (already handled above)
    # If no explicit contradiction, default to not_supported (RULE 3)
    verdict = "not_supported"
    reason = "Source does not carry this claim."
    return verdict, reason, candidates[0] if candidates else None, ()


def _has_explicit_opposition(claim: str, excerpt: str) -> bool:
    """Check if excerpt EXPLICITLY opposes the claim.
    
    Only returns True for explicit opposition, not mere absence of support.
    """
    claim_lower = claim.lower()
    excerpt_lower = excerpt.lower()
    
    # Check for explicit negation mismatch
    claim_has_negation = any(neg in claim_lower for neg in ["not ", "no ", "never ", "n't ", "cannot ", "doesn't ", "does not ", "won't ", "will not "])
    excerpt_has_negation = any(neg in excerpt_lower for neg in ["not ", "no ", "never ", "n't ", "cannot ", "doesn't ", "does not ", "won't ", "will not "])
    
    # If one has negation and other doesn't, check for opposite assertions
    if claim_has_negation != excerpt_has_negation:
        # One negates, other affirms - check if they're talking about same thing
        # Extract the positive form of the claim
        claim_positive = claim_lower
        for neg in ["not ", "no ", "never ", "n't ", "cannot ", "doesn't ", "does not ", "won't ", "will not "]:
            claim_positive = claim_lower.replace(neg, "")
        # If excerpt contains the positive form, it's opposition
        if any(word in excerpt_lower for word in claim_positive.split() if len(word) > 3):
            return True
    
    antonym_pairs = [
        ("increase", "decrease"), ("increase", "decline"), ("increase", "fall"), ("increase", "fell"),
        ("increased", "decreased"), ("increased", "declined"), ("increased", "fell"),
        ("increasing", "decreasing"), ("increasing", "declining"), ("increasing", "falling"),
        ("growth", "decline"), ("growth", "decrease"),
        ("profit", "loss"), ("gain", "loss"),
        ("increase", "reduce"), ("increased", "reduced"), ("rise", "fall"),
        ("up", "down"), ("higher", "lower"),
        ("exceed", "below"), ("above", "below"),
        ("profit", "deficit"), ("surplus", "deficit"),
        ("approve", "reject"), ("accept", "reject"),
        ("yes", "no"), ("true", "false"),
        # Insurance and policy vocabulary. The table above is financial, and a
        # policy states its coverage with include/exclude rather than with
        # increase/decrease — measured, "Coverage includes flood damage" against
        # "Coverage excludes flood damage" returned no opposition at all, so the
        # one state reserved for a source that says the opposite never fired on
        # the most ordinary policy contradiction there is.
        ("include", "exclude"), ("includes", "excludes"),
        ("included", "excluded"), ("including", "excluding"),
        ("cover", "exclude"), ("covers", "excludes"),
        ("covered", "excluded"), ("coverage", "exclusion"),
        ("affirm", "deny"), ("affirmed", "denied"),
        ("grant", "deny"), ("granted", "denied"),
        ("permit", "prohibit"), ("permitted", "prohibited"),
        ("allow", "disallow"), ("allowed", "disallowed"),
        ("applicable", "inapplicable"),
        ("eligible", "ineligible"),
        ("valid", "invalid"),
        ("within", "outside"),
        ("mandatory", "optional"),
        ("admitted", "denied"),
    ]

    # Whole words only. A substring test fires the pair ("cover", "exclude") on
    # the single sentence "Coverage excludes flood damage" against itself —
    # "cover" is inside "coverage" — so an identical claim and source were
    # reported contradicted. Measured before this guard: an ordinary
    # excludes-a-peril sentence contradicted itself.
    claim_words = set(_WORD_RE.findall(claim_lower))
    excerpt_words = set(_WORD_RE.findall(excerpt_lower))
    for w1, w2 in antonym_pairs:
        if w1 in claim_words and w2 in excerpt_words:
            return True
        if w2 in claim_words and w1 in excerpt_words:
            return True

    # A figure the claim states and the source states differently is an explicit
    # contradiction, not silence: "the deductible is 25,000" against a source
    # reading "the deductible is 50,000" is the source denying the claim, and
    # the lexical ratio below cannot see it because the surrounding words all
    # match. Compared as written values so "25,000" and "25000" agree.
    claim_numbers = _numeric_tokens(claim)
    excerpt_numbers = _numeric_tokens(excerpt)
    if claim_numbers and excerpt_numbers:
        if not (claim_numbers & excerpt_numbers):
            return True
    
    # Check for explicit contradiction phrases
    contradiction_phrases = [
        "contrary to", "opposite of", "contradicts", "refutes",
        "disproves", "opposite is true", "wrong", "incorrect",
        "not the case", "false", "inaccurate", "mistaken"
    ]
    for phrase in contradiction_phrases:
        if phrase in excerpt_lower and any(w in claim_lower for w in excerpt_lower.split() if len(w) > 3):
            return True
    
    return False

def _extract_supporting_quote(excerpt: str, claim: str) -> str:
    """Extract the most relevant sentence from excerpt supporting the claim."""
    sentences = re.split(r'(?<=[.!?])\s+', excerpt)
    claim_words = set(w.lower() for w in claim.split() if len(w) > 3)
    
    best_sentence = ""
    best_score = 0
    
    for sent in sentences:
        sent_lower = sent.lower()
        score = sum(1 for w in claim.split() if len(w) > 3 and w.lower() in sent_lower)
        if score > best_score:
            best_score = score
            best_sentence = sent
    
    return best_sentence if best_sentence else excerpt[:200]


def _extract_opposing_quote(excerpt: str, claim: str) -> str:
    """Extract the most relevant sentence opposing the claim."""
    sentences = re.split(r'(?<=[.!?])\s+', excerpt)
    
    for sent in sentences:
        if _has_explicit_opposition(claim, sent):
            return sent
    
    return excerpt[:200]


# ──────────────────────────────────────────────────────────────────────────────
# Fingerprinting
# ──────────────────────────────────────────────────────────────────────────────

def _compute_evidence_fingerprint(
    claim: str,
    candidates: list[dict[str, Any]],
    verdict: str,
    compile_type: str,
    truncation: dict[str, Any] | None,
) -> str:
    """Compute deterministic fingerprint of the evidence assembly."""
    payload = {
        "claim": claim,
        "verdict": verdict,
        "compile_type": compile_type,
        "candidates": [
            {
                "file_id": c.get("file_id"),
                "overlap_score": c.get("overlap_score"),
            }
            for c in candidates
        ],
        "truncation": truncation,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


# ──────────────────────────────────────────────────────────────────────────────
# Main Assembly Function
# ──────────────────────────────────────────────────────────────────────────────

def assemble_evidence(
    claim: str,
    substrate_rows: list[dict[str, Any]],
    compile_type: Literal["full", "selection"] = "full",
    selected_node_ids: list[str] | None = None,
    budget: EvidenceBudget | None = None,
    icp_profile: str | None = None,
) -> EvidenceAssemblyResult:
    """
    Assemble deterministic, provenance-rich evidence for a claim.
    
    This is the single entry point for evidence assembly.
    It separates:
    1. Source collection & budget enforcement
    2. Candidate anchor selection (lexical overlap only)
    3. Verdict classification (strict rules)
    4. Provenance preservation
    5. Deterministic output with fingerprint
    
    Args:
        claim: The claim to verify against sources
        substrate_rows: Source rows from Substrate Vault
        compile_type: "full" or "selection"
        selected_node_ids: Optional node IDs for selection compile
        budget: Optional evidence budget (per-file and total char limits)
    
    Returns:
        EvidenceAssemblyResult with verdict, anchors, quotes, provenance, fingerprint
    
    Raises:
        MissingRequiredInputError: If claim is missing
        InvalidCompileTypeError: If compile_type is invalid
        RequiredSourceUnavailableError: If required sources missing
    """
    # 1) Validate required input
    if not claim or not claim.strip():
        raise MissingRequiredInputError("claim is required and cannot be empty")
    
    # 2) Normalize inputs
    normalized_claim = _normalize_whitespace(claim)
    compile_type = compile_type.strip().lower()
    if compile_type not in EVIDENCE_COMPILE_TYPES:
        raise InvalidCompileTypeError(
            f"Invalid compile_type: {compile_type!r}. Must be 'full' or 'selection'."
        )
    
    # 3) Validate source availability
    if not substrate_rows:
        raise RequiredSourceUnavailableError("No substrate rows provided")
    
    # 4) Enforce source budget
    budget = budget or EvidenceBudget()
    ranked_rows = _rank_source_rows(substrate_rows, icp_profile)
    excerpts, truncation_summary = _enforce_source_budget(ranked_rows, budget)
    
    # 2) Candidate anchor selection (lexical overlap ONLY for candidate finding)
    candidates = _find_candidate_anchors(
        claim=claim,
        source_excerpts=excerpts,
        compile_type=compile_type,
        budget=budget,
    )
    
    # 3) Verdict classification (strict rules)
    verdict_str, reason, anchor, quotes = _classify_verdict(claim, candidates)
    
    verdict_obj = EvidenceVerdict(
        verdict=verdict_str,
        reason=reason,
        anchor=anchor,
        quotes=quotes,
    )
    
    # 5) Build provenance
    provenance = []
    for c in candidates[:5]:  # Limit to top 5 for provenance
        prov = {
            "file_id": c.get("file_id"),
            "filename": c.get("filename"),
            "page": c.get("page"),
            "section": c.get("section"),
            "row": c.get("row"),
            "trust": c.get("trust"),
            "truncated": c.get("truncated", False),
            "overlap_pct": c.get("overlap_pct"),
            "overlap_score": c.get("overlap_score"),
        }
        provenance.append(prov)
    
    # 6) Build truncation metadata
    truncation_occurred = truncation_summary.get("truncation_occurred", False)
    truncated_files = truncation_summary.get("truncated_files", [])
    total_chars_used = sum(len(e.get("excerpt", "")) for e in excerpts)
    truncation_meta = {
        "per_file_cap": EVIDENCE_PER_FILE_CHARS,
        "total_cap": EVIDENCE_TOTAL_CHARS,
        "total_chars_used": total_chars_used,
        "truncation_occurred": truncation_occurred,
        "truncated_files": truncated_files,
    }
    
    # 7) Compute fingerprint
    fingerprint = _compute_evidence_fingerprint(claim, candidates, verdict_str, compile_type, truncation_summary)
    
    # 8) Build result
    result = EvidenceAssemblyResult(
        claim=claim,
        verdict=EvidenceVerdict(
            verdict=verdict_str,
            reason=_build_reason(verdict_str),
            anchor=candidates[0] if candidates else None,
            quotes=_extract_quotes(candidates[0].get("excerpt", ""), claim) if candidates else (),
        ),
        anchors=tuple(c for c in candidates[:5]),
        quotes=_extract_quotes(candidates[0].get("excerpt", ""), "") if candidates else (),
        provenance=tuple(
            {
                "file_id": c.get("file_id"),
                "filename": c.get("filename"),
                "page": c.get("page"),
                "section": c.get("section"),
                "row": c.get("row"),
                "trust": c.get("trust"),
                "truncated": c.get("truncated", False),
            }
            for c in candidates[:5]
        ),
        truncation=truncation_meta,
        fingerprint=fingerprint,
        compile_type=compile_type,
    )
    
    return result


def _normalize_whitespace(text: str) -> str:
    """Normalize whitespace: collapse runs, strip ends."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip())


def _build_reason(verdict: str) -> str:
    """Build standard reason text for verdict."""
    reasons = {
        "supported": "Source explicitly carries the claim.",
        "partial": "Source supports part of the claim but misses qualifiers or details.",
        "not_supported": "Source does not carry this claim.",
        "contradicted": "Source explicitly states the opposite of the claim.",
        "unanchored": "No source span available for this claim.",
        "unverified": "Claim has not been verified against sources.",
    }
    return reasons.get(verdict, "")


def _extract_quotes(excerpt: str, claim: str) -> tuple[str, ...]:
    """Extract relevant sentences from excerpt."""
    if not excerpt:
        return ()
    sentences = re.split(r'(?<=[.!?])\s+', excerpt)
    claim_words = set(w.lower() for w in claim.split() if len(w) > 3)
    
    quotes = []
    for sent in sentences[:3]:
        if any(w in sent.lower() for w in claim.split() if len(w) > 3):
            quotes.append(sent.strip())
    
    return tuple(quotes[:2]) if quotes else (excerpt[:200],)


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

__all__ = [
    "assemble_evidence",
    "EvidenceAssemblyError",
    "MissingRequiredInputError",
    "InvalidCompileTypeError",
    "RequiredSourceUnavailableError",
    "EvidenceBudget",
    "EvidenceExcerpt",
    "EvidenceAnchor",
    "EvidenceVerdict",
    "EvidenceAssemblyResult",
    "EVIDENCE_TEMPLATE_VERSION",
    "PIPELINE_VERSION",
    "EVIDENCE_COMPILE_TYPES",
]