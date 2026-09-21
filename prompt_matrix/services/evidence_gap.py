"""Why a paragraph is unanchored, and what would ground it (2B).

The evidence drawer's unanchored state answers one question — *why is this claim
not grounded, and what would ground it?* — and the answer is a model's reading of
the claim against the closest sentences the lexical matcher refused. Those
near-misses are the real context: the gate already scored every source sentence,
so the ones that came close and did not clear the floor are exactly what the
model has to explain.

The model never fetches, never searches and never names a URL — the system rule
says so and ``parse_gap_analysis`` enforces it by stripping URL/domain tokens out
of every line and refusing an answer that loses a line to them. The third line
is the *query* the retrieval path (2C) runs; it is the model's only output that
leaves this module.

A failure is not dressed up: the caller shows the first line alone and the upload
button. There is no fallback analysis, because a fabricated reason is worse than
no reason.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

try:
    from ..cost_governance import CostGovernor, TaskType
    from ..models.jdf import _MIN_ANCHOR_COEFFICIENT, _MIN_ANCHOR_OVERLAP, _split_sentences, _tokenize
except ImportError:  # pragma: no cover
    from cost_governance import CostGovernor, TaskType
    from models.jdf import (  # type: ignore[no-redef]
        _MIN_ANCHOR_COEFFICIENT,
        _MIN_ANCHOR_OVERLAP,
        _split_sentences,
        _tokenize,
    )

_log = logging.getLogger(__name__)

# The system rule is the whole guard: the model is told what it is for and what
# it may not do with the outside world. Nothing in the user turn asks it to
# search, so a model that answers with a URL has gone outside the task.
GAP_SYSTEM = (
    "You analyze why a claim is not supported by a source. "
    "You do not fetch, search, or name URLs."
)

GAP_INSTRUCTIONS = (
    "Output exactly three lines:\n"
    "Missing: <why>\n"
    "Grounded by: <document category>\n"
    "Search query: <6-12 words; no URL, no domain>"
)

NEAR_MISS_SENTENCES = 3
NEAR_MISS_CHARS = 300
CLAIM_CHARS = 1200
MAX_QUERY_CHARS = 200

_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_DOMAIN_RE = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|gov|org|net|io|us|edu|co\.uk|info|biz)\b", re.IGNORECASE)
_LINE_RES = {
    "missing": re.compile(r"^\s*missing\s*:\s*(?P<value>.+?)\s*$", re.IGNORECASE | re.MULTILINE),
    "grounded_by": re.compile(r"^\s*grounded\s+by\s*:\s*(?P<value>.+?)\s*$", re.IGNORECASE | re.MULTILINE),
    "search_query": re.compile(r"^\s*search\s+query\s*:\s*(?P<value>.+?)\s*$", re.IGNORECASE | re.MULTILINE),
}


@dataclass(frozen=True)
class GapAnalysis:
    """The drawer's three lines. Every field is non-empty or the analysis is a failure."""

    missing: str
    grounded_by: str
    search_query: str
    model: str = ""

    def as_payload(self) -> dict[str, str]:
        return {
            "missing": self.missing,
            "grounded_by": self.grounded_by,
            "search_query": self.search_query,
            "model": self.model,
        }


def near_miss_sentences(claim: str, substrate_rows: list[dict[str, Any]]) -> list[str]:
    """The top ``NEAR_MISS_SENTENCES`` source sentences by token overlap with the claim.

    Every candidate is a sentence the anchoring gate already scored and refused:
    it clears ``_MIN_ANCHOR_OVERLAP`` on shared content tokens but not
    ``_MIN_ANCHOR_COEFFICIENT`` of the shorter text. Ranked by that coefficient,
    so the sentences shown are the closest the source came to supporting the
    claim. A sentence below the overlap floor is not shown — the gate never
    considered it a candidate, and the drawer must not imply it did.
    """
    clean_claim = str(claim or "").strip()
    if not clean_claim or not substrate_rows:
        return []
    claim_tokens = _tokenize(clean_claim)
    if not claim_tokens:
        return []
    scored: list[tuple[float, str]] = []
    for row in substrate_rows:
        text = str(row.get("extracted_text") or "")
        for sentence, _page in _split_sentences(text):
            candidate = str(sentence or "").strip()
            if not candidate:
                continue
            tokens = _tokenize(candidate)
            overlap = len(claim_tokens & tokens)
            if overlap < _MIN_ANCHOR_OVERLAP:
                continue
            score = overlap / min(len(claim_tokens), len(tokens)) if tokens else 0.0
            if score >= _MIN_ANCHOR_COEFFICIENT:
                continue                     # that one anchored the claim; not a miss
            scored.append((score, candidate))
    scored.sort(key=lambda item: item[0], reverse=True)
    out: list[str] = []
    for _score, sentence in scored[:NEAR_MISS_SENTENCES]:
        out.append(sentence[:NEAR_MISS_CHARS])
    return out


def build_gap_prompt(claim: str, near_misses: list[str]) -> str:
    """The user turn: the claim, the near-miss context, and the exact output shape."""
    context = "\n".join(near_misses) if near_misses else "(no source sentence came close)"
    return (
        f"Claim: {str(claim or '').strip()[:CLAIM_CHARS]}\n\n"
        f"Source context: {context}\n\n"
        f"{GAP_INSTRUCTIONS}"
    )


def strip_urls(text: str) -> str:
    """Remove URL and domain tokens from a line.

    The system rule forbids naming URLs; a model that names one anyway must not
    have it rendered — so the token goes, and a line it empties is a failed line.
    """
    without_urls = _URL_RE.sub(" ", str(text or ""))
    return _DOMAIN_RE.sub(" ", without_urls)


def parse_gap_analysis(raw: str, *, model: str = "") -> GapAnalysis | None:
    """Parse the three lines. ``None`` — never a partial analysis — on any miss."""
    text = str(raw or "").strip()
    if not text:
        return None
    values: dict[str, str] = {}
    for key, pattern in _LINE_RES.items():
        match = pattern.search(text)
        if match is None:
            return None
        value = " ".join(strip_urls(match.group("value")).split()).strip(" .;,")
        if not value:
            return None
        values[key] = value
    query = values["search_query"][:MAX_QUERY_CHARS].strip()
    if not query:
        return None
    return GapAnalysis(
        missing=values["missing"],
        grounded_by=values["grounded_by"],
        search_query=query,
        model=model,
    )


def _complete(prompt: str, project_id: str) -> tuple[str, str]:
    """One governed model call. Returns (raw_answer, model_id); raises on transport failure."""
    gov = CostGovernor()
    messages = [
        {"role": "system", "content": GAP_SYSTEM},
        {"role": "user", "content": prompt},
    ]
    policy = gov.preflight(project_id or "evidence_gap", TaskType.SUMMARIZE_NODE, messages)
    executor = gov.executor or gov._default_executor  # noqa: SLF001
    raw, in_tok, out_tok = executor(
        policy.litellm_model,
        messages,
        policy.max_output_tokens,
        policy.caching,
    )
    try:
        gov.record_usage(
            project_id or "evidence_gap",
            input_tokens=int(in_tok or 0),
            output_tokens=int(out_tok or 0),
            model_id=policy.model_id,
            task_type=TaskType.SUMMARIZE_NODE,
            meta={"pipeline": "evidence_gap_analysis"},
        )
    except Exception:   # accounting never changes the answer; the call already happened
        _log.warning("[evidence-gap] usage accounting failed for %s", project_id, exc_info=True)
    return str(raw or ""), policy.model_id


def analyze_gap(
    claim: str,
    substrate_rows: list[dict[str, Any]],
    *,
    project_id: str = "",
) -> GapAnalysis | None:
    """The gap analysis for one claim — ``None`` when the call or the parse failed.

    Never raises and never invents: the caller renders the failure path (the first
    line and the upload button) on ``None``.
    """
    near_misses = near_miss_sentences(claim, substrate_rows)
    prompt = build_gap_prompt(claim, near_misses)
    try:
        raw, model_id = _complete(prompt, project_id)
    except Exception as exc:
        _log.warning("[evidence-gap] call failed for %s: %s", project_id, exc)
        return None
    analysis = parse_gap_analysis(raw, model=model_id)
    if analysis is None:
        _log.warning(
            "[evidence-gap] unparseable answer for %s: %s",
            project_id,
            json.dumps(str(raw)[:200]),
        )
        return None
    return analysis
