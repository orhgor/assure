"""Local quality scores for a finished reply.

These are measurements on this machine, not a published benchmark.
Consensus is lexical overlap of ensemble drafts. Coherence is a structure
heuristic. Hallucination rate uses the citation scrubber. No extra model call
unless PEM_QUALITY_JUDGE=1, which is off by default and still not implemented
as a hidden Send (judge stays unused so quota and cost stay honest).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

try:
    from .agents.critique import run_critique
    from .agents.rule_critic import rule_based_critique
except ImportError:
    from agents.critique import run_critique
    from agents.rule_critic import rule_based_critique

_WORD = re.compile(r"[a-z0-9]{2,}", re.I)
_SENTENCE = re.compile(r"[^.!?]+[.!?]|[^.!?]+$", re.S)
_HEADING = re.compile(r"(?m)^(?:#{1,3}\s+\S|.{0,80}:\s*$|\*\*[A-Z].{2,40}\*\*)")


@dataclass
class QualityScore:
    consensus_score: float | None
    coherence_score: float
    hallucination_rate: float
    token_efficiency: float
    overall_score: float
    flagged_count: int = 0
    draft_count: int = 0
    method: dict | None = None

    def as_dict(self) -> dict:
        payload = asdict(self)
        return payload


def _words(text: str) -> set[str]:
    return set(w.lower() for w in _WORD.findall(text or ""))


def consensus_from_drafts(drafts: list[str]) -> float | None:
    texts = [item.strip() for item in drafts if item and item.strip()]
    if len(texts) < 2:
        return None
    scores: list[float] = []
    for i, left in enumerate(texts):
        for right in texts[i + 1 :]:
            a, b = _words(left), _words(right)
            if not a and not b:
                scores.append(1.0)
            elif not a or not b:
                scores.append(0.0)
            else:
                scores.append(len(a & b) / len(a | b))
    if not scores:
        return None
    return round(sum(scores) / len(scores), 4)


def coherence_score(reply: str) -> float:
    text = reply or ""
    words = text.split()
    n = len(words)
    score = 0.35
    if n >= 80:
        score += 0.15
    elif n < 25:
        score -= 0.2
    if _HEADING.search(text) or re.search(r"(?m)^(?:- |\* |\d+\.\s)", text):
        score += 0.2
    if "|" in text and "---" in text:
        score += 0.1
    findings = rule_based_critique(text)
    score -= 0.12 * min(3, len(findings.get("errors") or []))
    score -= 0.05 * min(3, len(findings.get("warnings") or []))
    sentences = [s.strip() for s in _SENTENCE.findall(text) if s.strip()]
    if sentences:
        avg = sum(len(s.split()) for s in sentences) / len(sentences)
        if 8 <= avg <= 40:
            score += 0.12
    return round(max(0.0, min(1.0, score)), 4)


def hallucination_rate(reply: str, context: str) -> float:
    rate, _flagged = hallucination_details(reply, context)
    return rate


def hallucination_details(reply: str, context: str) -> tuple[float, int]:
    text = reply or ""
    sentences = [s.strip() for s in _SENTENCE.findall(text) if s.strip()]
    if not sentences:
        return 0.0, 0
    verdict = run_critique(text, context)
    flagged = list(verdict.get("flagged_lines") or [])
    claim_n = 0
    for sent in sentences:
        if "data not available" in sent.lower():
            continue
        claim_n += 1
    if claim_n <= 0:
        return 0.0, len(flagged)
    return round(min(1.0, len(flagged) / claim_n), 4), len(flagged)


def token_efficiency(core: float, total_tokens: int) -> float:
    # Quality per 400 tokens, capped at 1. Higher is better.
    tokens = max(int(total_tokens or 0), 1)
    return round(min(1.0, (core * 400.0) / tokens), 4)


def score_run(
    *,
    reply: str,
    context: str = "",
    drafts: list[str] | None = None,
    total_tokens: int = 0,
) -> QualityScore:
    consensus = consensus_from_drafts(drafts or [])
    coherence = coherence_score(reply)
    hall, flagged = hallucination_details(reply, context)
    draft_n = len([item for item in (drafts or []) if item and str(item).strip()])
    if consensus is None:
        core = (coherence + (1.0 - hall)) / 2.0
    else:
        core = (consensus + coherence + (1.0 - hall)) / 3.0
    efficiency = token_efficiency(core, total_tokens)
    if consensus is None:
        overall = 0.45 * coherence + 0.40 * (1.0 - hall) + 0.15 * efficiency
    else:
        overall = (
            0.35 * consensus
            + 0.30 * coherence
            + 0.25 * (1.0 - hall)
            + 0.10 * efficiency
        )
    return QualityScore(
        consensus_score=consensus,
        coherence_score=coherence,
        hallucination_rate=hall,
        token_efficiency=efficiency,
        overall_score=round(overall, 4),
        flagged_count=flagged,
        draft_count=draft_n,
        method={
            "consensus": "lexical_jaccard_drafts" if consensus is not None else "n/a_single_draft",
            "coherence": "structure_heuristic",
            "hallucination": "citation_scrubber",
            "judge": False,
        },
    )


def draft_replies(steps) -> list[str]:
    out: list[str] = []
    for step in steps or []:
        name = getattr(step, "name", "") or ""
        reply = getattr(step, "reply", None)
        if reply and (name.startswith("draft") or name in {"create", "attempt"}):
            out.append(reply)
    return out
