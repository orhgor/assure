"""Hardcoded FINAL layout: verified from file vs inferred from training."""

from __future__ import annotations

import re
from typing import Any

SOURCE_UPLOADED = "uploaded_file"
SOURCE_INFERRED = "llm_training_data"
EMPTY_VERIFIED = "Data not available in current context."
EMPTY_INFERRED = "Data not available."
EMPTY_QUESTIONS = "Data not available."

_HEADING = re.compile(
    r"^\s*(?:\*\*)?(?:thesis|verified findings|verified from context|"
    r"inferred gaps|logical inference|findings|open questions(?: for engineering)?)"
    r"(?:\*\*)?\s*:?\s*$",
    re.IGNORECASE,
)


def _bullet(text: str) -> str:
    line = text.strip()
    if line.startswith(("- ", "* ", "• ")):
        return line
    return f"- {line}"


def _items(findings: Any, source: str) -> list[str]:
    if findings is None:
        return []
    if isinstance(findings, str):
        lines = [part.strip() for part in findings.split("\n") if part.strip()]
        if source == SOURCE_UPLOADED:
            return lines
        return []
    out: list[str] = []
    for item in findings:
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append(text)
            continue
        if not isinstance(item, dict):
            continue
        item_source = str(item.get("source") or item.get("origin") or "")
        if item_source and item_source != source:
            continue
        if not item_source and source != SOURCE_UPLOADED:
            continue
        text = str(item.get("text") or item.get("finding") or "").strip()
        if text:
            out.append(text)
    return out


def generate_verified_section(findings: Any, source: str = SOURCE_UPLOADED) -> str:
    items = _items(findings, source)
    if not items:
        return EMPTY_VERIFIED
    return "\n".join(_bullet(item) for item in items)


def generate_inferred_section(findings: Any, source: str = SOURCE_INFERRED) -> str:
    items = _items(findings, source)
    if not items:
        return EMPTY_INFERRED
    return "\n".join(_bullet(item) for item in items)


def format_final_output(thesis: Any, findings: Any, questions: Any) -> str:
    thesis_text = str(thesis or "").strip() or EMPTY_INFERRED
    questions_text = _format_questions(questions)
    return (
        f"**Thesis:** {thesis_text}\n"
        "\n"
        "---\n"
        "**VERIFIED FINDINGS (Directly from uploaded context):**\n"
        f"{generate_verified_section(findings, source=SOURCE_UPLOADED)}\n"
        "\n"
        "---\n"
        "**INFERRED GAPS (Logical inference; requires manual validation):**\n"
        f"{generate_inferred_section(findings, source=SOURCE_INFERRED)}\n"
        "*Note: These are logical projections, not verified external facts.*\n"
        "\n"
        "---\n"
        "**OPEN QUESTIONS:**\n"
        f"{questions_text}\n"
    )


def shape_final_reply(draft: str | None, user_context: Any = None) -> str:
    """Force any model draft into the hardcoded FINAL sections."""
    parsed = parse_final_parts(draft or "")
    findings = classify_findings(parsed["findings"], user_context)
    return format_final_output(parsed["thesis"], findings, parsed["questions"])


def parse_final_parts(draft: str) -> dict[str, Any]:
    text = (draft or "").strip()
    if not text:
        return {"thesis": "", "findings": [], "questions": ""}

    buckets = {"thesis": [], "verified": [], "inferred": [], "findings": [], "questions": []}
    current = "findings"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        key = _heading_key(line)
        if key:
            current = key
            rest = _heading_rest(line)
            if rest:
                buckets[current].append(rest)
                if key == "thesis":
                    current = "findings"
            continue
        buckets[current].append(_strip_bullet(line))

    thesis = " ".join(buckets["thesis"]).strip()
    if not thesis:
        thesis = buckets["findings"][0] if buckets["findings"] else ""
        if thesis and buckets["findings"]:
            buckets["findings"] = buckets["findings"][1:]

    findings: list[dict[str, str]] = []
    for item in buckets["verified"]:
        findings.append({"text": item, "source": SOURCE_UPLOADED})
    for item in buckets["inferred"]:
        findings.append({"text": item, "source": SOURCE_INFERRED})
    for item in buckets["findings"]:
        if item == thesis:
            continue
        findings.append({"text": item, "source": ""})

    questions = buckets["questions"]
    if not questions:
        questions = [item for item in buckets["findings"] if "?" in item]
        findings = [item for item in findings if "?" not in item.get("text", "")]

    return {
        "thesis": thesis,
        "findings": findings,
        "questions": questions,
    }


def classify_findings(findings: list[Any], user_context: Any) -> list[dict[str, str]]:
    allowed = _context_text(user_context).casefold()
    out: list[dict[str, str]] = []
    for item in findings:
        if isinstance(item, str):
            text, source = item.strip(), ""
        elif isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            source = str(item.get("source") or "")
        else:
            continue
        if not text:
            continue
        if source in {SOURCE_UPLOADED, SOURCE_INFERRED}:
            out.append({"text": text, "source": source})
            continue
        if allowed and _supported_by_context(text, allowed):
            out.append({"text": text, "source": SOURCE_UPLOADED})
        else:
            out.append({"text": text, "source": SOURCE_INFERRED})
    return out


def _format_questions(questions: Any) -> str:
    if questions is None:
        return EMPTY_QUESTIONS
    if isinstance(questions, str):
        lines = [part.strip() for part in questions.split("\n") if part.strip()]
    else:
        lines = [str(item).strip() for item in questions if str(item).strip()]
    if not lines:
        return EMPTY_QUESTIONS
    return "\n".join(_bullet(item) for item in lines)


def _heading_key(line: str) -> str | None:
    folded = re.sub(r"[*_#]+", "", line).strip().casefold()
    if folded.startswith("thesis"):
        return "thesis"
    if folded.startswith("verified"):
        return "verified"
    if folded.startswith("inferred") or folded.startswith("logical inference"):
        return "inferred"
    if folded.startswith("open question"):
        return "questions"
    if folded.startswith("findings"):
        return "findings"
    return None


def _heading_rest(line: str) -> str:
    if ":" not in line:
        return ""
    rest = line.split(":", 1)[1].strip()
    return _strip_bullet(rest)


def _strip_bullet(line: str) -> str:
    return re.sub(r"^(?:[-*•]\s+|\d+\.\s+)", "", line).strip()


def _context_text(user_context: Any) -> str:
    if user_context is None:
        return ""
    if isinstance(user_context, str):
        return user_context
    if isinstance(user_context, dict):
        for key in ("uploaded_file_content", "context", "text"):
            value = user_context.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return str(user_context)


def _supported_by_context(finding: str, allowed_fold: str) -> bool:
    needle = _strip_bullet(finding).casefold()
    if len(needle) < 12:
        return needle in allowed_fold
    if needle in allowed_fold:
        return True
    words = [word for word in re.findall(r"[a-z0-9]+", needle) if len(word) > 3]
    if len(words) < 4:
        return False
    hits = sum(1 for word in words if word in allowed_fold)
    return hits / len(words) >= 0.7
