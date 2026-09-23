"""Local quality scores for a finished reply.

These are measurements on this machine, not a published benchmark.
Consensus is lexical overlap of ensemble drafts. Coherence is a structure
heuristic. Hallucination rate uses the citation scrubber. No extra model call
unless PEM_QUALITY_JUDGE=1, which is off by default and still not implemented
as a hidden Send (judge stays unused so quota and cost stay honest).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

try:
    from .agents.critique import run_critique
    from .agents.final import _heading_key, _supported_by_context
    from .agents.rule_critic import rule_based_critique
except ImportError:
    from agents.critique import run_critique
    from agents.final import _heading_key, _supported_by_context
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
    grounded_spans: list = field(default_factory=list)
    inferred_spans: list = field(default_factory=list)

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


def audit_body(reply: str) -> str:
    """Text the highlighter measures. Structured JSON uses the answer field."""
    text = reply or ""
    blob = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", blob, re.S)
    if fence:
        blob = fence.group(1).strip()
    try:
        obj = json.loads(blob)
    except (TypeError, ValueError):
        return text
    if isinstance(obj, dict):
        answer = obj.get("answer")
        if isinstance(answer, str) and answer.strip():
            return answer
    return text


def audit_spans(reply: str, context: str) -> dict[str, list[dict[str, int]]]:
    """Character offsets for grounded (file-backed) vs inferred sentences.

    Empty lists when there is no reply or no file context so the UI hides the
    legend. Section headings are skipped (neutral). A body line is grounded
    only when enough of its words appear in the files — a VERIFIED heading
    does not paint the rest of the section green.
    """
    text = audit_body(reply)
    ctx = (context or "").strip()
    grounded: list[dict[str, int]] = []
    inferred: list[dict[str, int]] = []
    if not text.strip() or not ctx:
        return {"grounded_spans": grounded, "inferred_spans": inferred}
    allowed = ctx.casefold()

    def line_grounded(line: str) -> bool:
        if _supported_by_context(line, allowed):
            return True
        words = [word for word in re.findall(r"[a-z0-9]+", line.casefold()) if len(word) > 2]
        if len(words) < 2:
            return False
        hits = sum(1 for word in words if word in allowed)
        return hits / len(words) >= 0.5

    pos = 0
    for raw in text.splitlines(keepends=True):
        start = pos
        end = pos + len(raw)
        pos = end
        stripped = raw.strip()
        if not stripped:
            continue
        if "data not available" in stripped.casefold():
            continue
        if _heading_key(stripped):
            continue
        folded = re.sub(r"^[#*_\s]+", "", stripped).casefold()
        if folded.startswith(("verified findings", "inferred", "thesis", "open questions")):
            continue
        kind = "grounded" if line_grounded(stripped) else "inferred"
        vis_end = end
        while vis_end > start and text[vis_end - 1] in "\r\n":
            vis_end -= 1
        if vis_end <= start:
            continue
        item = {"start": start, "end": vis_end}
        if kind == "grounded":
            grounded.append(item)
        else:
            inferred.append(item)
    return {"grounded_spans": grounded, "inferred_spans": inferred}


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
        overall = 0.35 * consensus + 0.30 * coherence + 0.25 * (1.0 - hall) + 0.10 * efficiency
    spans = audit_spans(reply, context)
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
        grounded_spans=spans["grounded_spans"],
        inferred_spans=spans["inferred_spans"],
    )


PENDING = "Confidence check pending. Review claims against your files."

_MODEL_LABEL = {
    "gemini": "Gemini",
    "claude": "Claude",
    "kimi": "Kimi",
    "ollama": "a runner closed to the internet",
    "cursor": "Cursor",
}


def _model_label(name: str) -> str:
    key = (name or "").strip().lower()
    return _MODEL_LABEL.get(key, name or "the model")


def _join_models(models: list[str]) -> str:
    labels = [_model_label(item) for item in models if item]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return ", ".join(labels[:-1]) + ", and " + labels[-1]


def confidence_text(
    *,
    quality: dict | None = None,
    models: list[str] | None = None,
    workflow: str = "",
) -> str:
    """One sentence for every Send. Never empty."""
    q = quality if isinstance(quality, dict) else {}
    names = [item for item in (models or []) if item]
    flagged = q.get("flagged_count")
    score = q.get("consensus_score")
    draft_n = int(q.get("draft_count") or 0)
    n_flag = 0 if flagged is None else int(flagged)
    joined = _join_models(names)

    if not q:
        return PENDING
    if score is not None:
        pct = int(round(float(score) * 100))
        who = joined if len(names) >= 2 else "Models"
        return f"{who} agree on {pct}%. {n_flag} claims were flagged as unsupported."
    if names:
        return (
            f"Answer from {joined or _model_label(names[0])}. "
            f"{n_flag} claims were checked against your files."
        )
    if flagged is None and score is None and draft_n < 2:
        return PENDING
    return f"Answer from one model. {n_flag} claims were checked against your files."


def models_from_steps(steps, primary: str | None = None) -> list[str]:
    names: list[str] = []
    for step in steps or []:
        label = getattr(step, "name", "") or ""
        target = getattr(step, "target_ai", None)
        if not target:
            continue
        if label.startswith("draft") or label in {"create", "attempt", "draft"}:
            if target not in names:
                names.append(target)
    if primary and primary not in names:
        names.insert(0, primary)
    return names


def models_used_from_steps(steps) -> list[str]:
    """Models that returned a successful reply (no error / ERROR prefix)."""
    names: list[str] = []
    for step in steps or []:
        target = getattr(step, "target_ai", None)
        if not target or target in {"pem", "combine"}:
            continue
        if getattr(step, "error", None):
            continue
        reply = getattr(step, "reply", None)
        if not reply or str(reply).strip().startswith("ERROR:"):
            continue
        label = getattr(step, "name", "") or ""
        if label.startswith("draft") or label in {"create", "attempt", "final", "draft"}:
            if target not in names:
                names.append(target)
    return names


def draft_replies(steps) -> list[str]:
    out: list[str] = []
    for step in steps or []:
        name = getattr(step, "name", "") or ""
        reply = getattr(step, "reply", None)
        if reply and (name.startswith("draft") or name in {"create", "attempt"}):
            out.append(reply)
    return out
