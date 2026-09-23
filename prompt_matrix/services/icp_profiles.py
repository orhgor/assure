"""ICP prompting profiles — how the compiler biases one audience's answers.

A profile changes emphasis, never the grounding contract: which spans rank
first, and which sections the answer is expected to carry. The gates that
refuse an ungrounded draft are the compiler's, not a profile's, so adding a
profile cannot loosen them.

The profile is passed through to the assembled prompt and recorded in its
metadata, so a cached compile cannot serve one audience's answer to another.
"""

from __future__ import annotations

import re
from typing import Any


#: Policy/claim vocabulary, weighted by how much the term matters to claim
#: triage. Weights are relative: ranks are compared within a confidence bucket,
#: so the absolute scale only decides ordering among spans of equal quality.
REAL_ESTATE_INSURANCE_CLAIM_MANAGER: dict[str, Any] = {
    "name": "real_estate_insurance_claim_manager",
    "audience": "Real estate insurance claim manager",
    "goal": (
        "Produce claim-ready, policy-grounded output covering limits, "
        "deductibles, exclusions, endorsements, insured property and the "
        "evidence gaps that remain."
    ),
    "priority_keywords": {
        "declarations": 0.16,
        "coverage": 0.18,
        "limit": 0.18,
        "limits": 0.18,
        "deductible": 0.18,
        "deductibles": 0.18,
        "exclusion": 0.18,
        "exclusions": 0.18,
        "endorsement": 0.16,
        "endorsements": 0.16,
        "insured": 0.08,
        "property": 0.14,
        "location": 0.14,
        "loss date": 0.12,
        "date of loss": 0.12,
        "cause of loss": 0.14,
        "claim": 0.10,
        "adjuster": 0.10,
        "reservation of rights": 0.14,
        "replacement cost": 0.12,
        "actual cash value": 0.12,
        "named peril": 0.12,
        "additional insured": 0.10,
        "mortgagee": 0.08,
        "loss payee": 0.08,
        "schedule": 0.10,
        "coverage form": 0.12,
        "building": 0.08,
        "business income": 0.08,
        "civil authority": 0.08,
        "policy": 0.10,
    },
    "response_contract": {
        "sections": [
            "claim_snapshot",
            "policy_snapshot",
            "coverage_and_exclusions",
            "evidence_table",
            "missing_items",
            "confidence",
        ],
        "tone": "concise, operational, auditable",
        "style_rules": [
            "Do not over-explain.",
            "Prioritise claim decision utility.",
            "Prefer exact policy wording when the source supports it.",
            "Separate supported facts from missing facts.",
            "Flag weak OCR or low-confidence parse spans explicitly.",
        ],
    },
}

#: Every profile, by name. The default is resolved from this map rather than
#: repeated, so an unknown name falls back instead of raising mid-compile.
PROFILES: dict[str, dict[str, Any]] = {
    REAL_ESTATE_INSURANCE_CLAIM_MANAGER["name"]: REAL_ESTATE_INSURANCE_CLAIM_MANAGER,
}

DEFAULT_PROFILE = REAL_ESTATE_INSURANCE_CLAIM_MANAGER["name"]

#: Multi-word keys are matched as phrases; single words are matched on word
#: boundaries. A bare substring test makes "limit" fire inside "limitation"
#: and "claim" inside "claimant", which is most of a policy document — the
#: boost then stops discriminating between spans.
_WORD_RE = re.compile(r"[a-z0-9]+")


def get_icp_profile(name: str | None) -> dict[str, Any]:
    """The profile for ``name``, falling back to the default when unknown."""
    if name and name in PROFILES:
        return PROFILES[name]
    return PROFILES[DEFAULT_PROFILE]


def _match_words(text_words: set[str], text_l: str, keyword: str) -> bool:
    if " " in keyword:
        return keyword in text_l
    return keyword in text_words


def icp_keyword_boost(text: str, profile_name: str | None = None) -> float:
    """How strongly ``text`` reads as this profile's material, in [0, 1].

    Deterministic and dependency-free: the same text and profile always score
    the same, so a ranked span list is reproducible and cacheable.
    """
    profile = get_icp_profile(profile_name)
    text_l = (text or "").lower()
    if not text_l:
        return 0.0
    words = set(_WORD_RE.findall(text_l))
    score = 0.0
    for keyword, weight in profile.get("priority_keywords", {}).items():
        if _match_words(words, text_l, keyword):
            score += float(weight)
    return min(score, 1.0)


def icp_prompt_contract(profile_name: str | None = None) -> dict[str, Any]:
    """The audience, goal and response contract, for embedding in a prompt."""
    profile = get_icp_profile(profile_name)
    return {
        "name": profile["name"],
        "audience": profile["audience"],
        "goal": profile["goal"],
        "response_contract": profile["response_contract"],
    }


def icp_prompt_block(profile_name: str | None = None) -> str:
    """The profile as prompt lines, appended to the compile instructions.

    Kept separate from the grounding contract on purpose: this block may name
    sections and style, and it must not restate or weaken the rules that decide
    whether a draft is grounded — those are the compiler's and are identical
    for every profile.
    """
    contract = icp_prompt_contract(profile_name)
    rc = contract["response_contract"]
    lines = [
        f"You are writing for: {contract['audience']}.",
        f"Goal: {contract['goal']}",
        "",
        "Prioritise: coverage, limits, deductibles, exclusions, endorsements, "
        "insured property, location, loss date, cause of loss, and the claim- "
        "critical facts the source does not state.",
        "",
        "Expected sections:",
    ]
    for section in rc["sections"]:
        lines.append(f"- {section}")
    lines.append("")
    lines.append("Style:")
    for rule in rc["style_rules"]:
        lines.append(f"- {rule}")
    lines.append(f"- Tone: {rc['tone']}.")
    return "\n".join(lines)
