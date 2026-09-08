"""Defensive parsing for strict [claim: N] LLM citation tags."""

from __future__ import annotations

import re

# Matches [claim: 1], [Claim: 1], [claim:1], trailing tag at end of claim text.
_CLAIM_TAG_PATTERN = re.compile(r"(.*?)(?:\[[cC]laim:\s*(\d+)\])", re.DOTALL)


def extract_claims_from_stream(llm_output: str) -> list[dict[str, str]]:
    """
    Parse generated text for claim tags and extract the preceding claim text
    for groundrails verification.
    """
    text = llm_output or ""
    extracted: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    for match in _CLAIM_TAG_PATTERN.finditer(text):
        claim_text = match.group(1).strip()
        clean_text = re.sub(r"^\s+", "", claim_text)
        if clean_text:
            # Prefer the sentence immediately before the tag.
            parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", clean_text) if p.strip()]
            clean_text = parts[-1] if parts else clean_text

        claim_num = match.group(2)
        claim_id = f"claim_{claim_num}"
        if not clean_text or claim_id in seen_ids:
            continue
        seen_ids.add(claim_id)
        extracted.append({"claim_id": claim_id, "text": clean_text})

    return extracted
