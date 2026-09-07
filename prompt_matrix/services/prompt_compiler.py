"""Prompt Compiler — deterministic, fully-specified prompts (no LLM)."""

from __future__ import annotations

import json
from typing import Any

_JDF_SCHEMA_HINT = {
    "document_id": "string",
    "meta": {"project_id": "string", "intent_type": "string"},
    "truth_ledger": {"metric_name": "numeric_value"},
    "body": [
        {
            "type": "section",
            "id": "string",
            "title": "string",
            "children": [
                {
                    "type": "paragraph",
                    "id": "string",
                    "content": "markdown string",
                    "meta": {"locks": []},
                }
            ],
        }
    ],
}

_INTENT_INSTRUCTIONS = {
    "extract": "Extract only factual claims supported by the sources. Prefer bullet lists.",
    "compare": "Compare entities or metrics across sources. Highlight agreements and conflicts.",
    "audit": "Audit claims for verifiability. Flag unsupported statements explicitly.",
    "draft": "Draft a concise business document grounded in the sources.",
    "unknown": "Produce a concise, source-grounded answer.",
}


def compile_prompt(intent: str, intent_type: str, sources: list[dict[str, Any]]) -> str:
    """Build a fully-specified prompt with grounding constraints and JDF schema."""
    text = (intent or "").strip()
    itype = (intent_type or "unknown").strip().lower()
    if itype not in _INTENT_INSTRUCTIONS:
        itype = "unknown"

    source_blocks: list[str] = []
    for i, src in enumerate(sources or [], start=1):
        name = str(src.get("name") or src.get("id") or f"source-{i}")
        excerpt = str(src.get("excerpt") or src.get("extracted_text") or src.get("content") or "")
        web_tag = " [web]" if src.get("web") else ""
        source_blocks.append(f"### Source {i}: {name}{web_tag}\n{excerpt[:4000]}")

    sources_section = (
        "\n\n".join(source_blocks)
        if source_blocks
        else "No vault sources attached. Use only verifiable public facts; mark unknowns."
    )

    schema_json = json.dumps(_JDF_SCHEMA_HINT, indent=2)
    task_line = _INTENT_INSTRUCTIONS[itype]

    return (
        "You are Assure Auto-Compiler. Follow ALL constraints exactly.\n\n"
        f"## Task ({itype})\n{task_line}\n\n"
        f"## User intent\n{text}\n\n"
        "## Source grounding constraints\n"
        "- Use ONLY the provided sources for factual claims.\n"
        "- If a fact is not in the sources, write: [NOT FOUND IN SOURCES].\n"
        "- Do not invent numbers, dates, or citations.\n"
        "- Negative space: when evidence is missing, say so explicitly.\n\n"
        f"## Sources\n{sources_section}\n\n"
        "## Output format\n"
        "Return prose suitable for conversion to JDF (markdown headings allowed).\n"
        "After the prose, append a fenced JSON block `assure-jdf` with this AST shape:\n"
        f"{schema_json}\n"
    )
