"""Deterministic citation critique. Rejects hallucinated sources the LLM prompt missed."""

from __future__ import annotations

import re
from typing import Any

REPLACEMENT = "Data not available in current context."

BANNED_PATTERNS = [
    re.compile(
        r"\b\d{1,3}%\b.*?(?:revised|retracted|issued|published)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:September|Sept|January|Jan|February|Feb|March|Mar|April|Apr|May|"
        r"June|Jun|July|Jul|August|Aug|October|Oct|November|Nov|December|Dec)\s+20\d{2}",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:Content Marketing Institute|LegalZoom|Influencer Marketing Hub|r/publichealth)",
        re.IGNORECASE,
    ),
]


def _allowed_text(user_context: Any) -> str:
    if user_context is None:
        return ""
    if isinstance(user_context, str):
        return user_context
    if isinstance(user_context, dict):
        for key in ("uploaded_file_content", "context", "text"):
            value = user_context.get(key)
            if isinstance(value, str) and value.strip():
                return value
        chunks = [str(value) for value in user_context.values() if isinstance(value, str)]
        return "\n".join(chunks)
    return str(user_context)


def run_critique(draft_output: str | None, user_context: Any) -> dict[str, Any]:
    """Critiques the draft and rejects hallucinated external sources."""
    draft = draft_output or ""
    allowed = _allowed_text(user_context)
    allowed_fold = allowed.casefold()
    flagged_lines: list[str] = []
    seen: set[str] = set()

    for line in draft.split("\n"):
        stripped = line.strip()
        if not stripped or stripped in seen:
            continue
        for pattern in BANNED_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            span = match.group(0).strip()
            if span.casefold() in allowed_fold or stripped in allowed:
                continue
            flagged_lines.append(stripped)
            seen.add(stripped)
            break

    if flagged_lines:
        return {
            "status": "REJECTED",
            "reason": f"Hallucinated sources detected: {flagged_lines}",
            "action": f"Remove these lines and replace with: '{REPLACEMENT}'",
            "flagged_lines": flagged_lines,
        }
    return {"status": "APPROVED", "draft": draft}


def scrub_draft(draft_output: str, flagged_lines: list[str]) -> str:
    flagged = {line.strip() for line in flagged_lines if line.strip()}
    if not flagged:
        return draft_output
    out: list[str] = []
    for line in draft_output.split("\n"):
        if line.strip() in flagged:
            out.append(REPLACEMENT)
        else:
            out.append(line)
    return "\n".join(out)


def enforce(draft_output: str | None, user_context: Any) -> tuple[str, dict[str, Any]]:
    draft = draft_output or ""
    verdict = run_critique(draft, user_context)
    if verdict["status"] != "REJECTED":
        return draft, verdict
    clean = scrub_draft(draft, list(verdict.get("flagged_lines") or []))
    return clean, verdict
