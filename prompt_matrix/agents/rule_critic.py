"""Local, zero-API critic for air-gapped red-hat (--critic rule)."""

from __future__ import annotations

import os
import re
from typing import Dict, List


def rule_critic_requested(critic: str | None = None) -> bool:
    if (critic or "").strip().lower() == "rule":
        return True
    return os.getenv("PEM_CRITIC_MODE", "").strip().lower() == "rule"


def rule_based_critique(prompt_text: str) -> Dict[str, List[str]]:
    """
    Runs static analysis on the prompt.
    Inspired by promptdiff: catches conflicting roles, ambiguous refs, word limits vs examples.
    Returns a dict of findings.
    """
    findings: Dict[str, List[str]] = {
        "errors": [],
        "warnings": [],
        "suggestions": [],
    }
    text = prompt_text or ""

    if re.search(r"act as.*expert", text, re.I) and re.search(r"be concise|brief", text, re.I):
        findings["warnings"].append(
            "Conflict: 'expert' often implies depth, but 'concise' restricts detail."
        )

    if re.search(r"\bit\b", text) and not re.search(r"the\s+(\w+)\s+it", text):
        findings["suggestions"].append("Ambiguous 'it' found. Replace with specific noun.")

    limit_match = re.search(r"(\d+)\s*(words?|tokens?)", text, re.I)
    if limit_match:
        limit_num = int(limit_match.group(1))
        example_sentences = re.findall(r"Example:?\s*([^.\n]+[.\n])", text, re.I)
        if example_sentences:
            avg_example_len = sum(len(s.split()) for s in example_sentences) / len(example_sentences)
            if avg_example_len > limit_num * 0.8:
                findings["errors"].append(
                    f"Word limit {limit_num} is too tight for examples averaging {avg_example_len:.0f} words."
                )

    declared = set(re.findall(r"\{(\w+)\}", text))
    used = set(re.findall(r"\{\{(\w+)\}\}", text))
    if declared and not used:
        findings["warnings"].append(f"Variables declared but never used: {declared}")
    if used and not declared:
        findings["errors"].append(f"Variables used but never declared: {used}")

    if re.search(r"formal|professional", text, re.I) and re.search(r"slang|casual|colloquial", text, re.I):
        findings["errors"].append("Contradictory tone constraints: Formal vs Casual.")

    return findings


def format_rule_critique(findings: dict) -> str:
    output = "### Rule-Based Critic (Deterministic, No API Call)\n\n"
    if not any(findings.values()):
        return output + "No structural issues detected. Proceed."
    for level, items in findings.items():
        if items:
            output += f"**{level.upper()}:**\n"
            for item in items:
                output += f"- {item}\n"
    return output


def critique_prompt(prompt_text: str, draft: str | None = None) -> str:
    blob = prompt_text or ""
    if draft:
        blob = f"{blob}\n\n===== DRAFT =====\n{draft}"
    return format_rule_critique(rule_based_critique(blob))
