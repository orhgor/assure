"""Prompt Compiler — strict [claim: N] citation envelope for groundrails."""

from __future__ import annotations

_SYSTEM_PROMPT = """You are a deterministic clinical AI architect drafting a compliance-ready document.
You must synthesize the provided source documents based on the user's directive.

CRITICAL CONSTRAINTS:
1. NO EXTERNAL KNOWLEDGE: You may only use facts, metrics, and claims explicitly stated in the SOURCE DOCUMENTS.
2. MANDATORY CITATIONS: Every factual claim, metric, or technical specification MUST be followed immediately by a citation tag.
3. STRICT SYNTAX: The citation tag must be formatted EXACTLY as [claim: N], where N is a sequentially increasing integer starting at 1.

EXAMPLES OF CORRECT USAGE:
The Corsano CardioWatch samples electrodermal activity at 4Hz [claim: 1]. It utilizes a 24-bit ADC for high resolution [claim: 2].

EXAMPLES OF INCORRECT USAGE:
[claim: 1] The watch has a 4Hz rate. (INCORRECT: Tag must be at the end of the claim).
The watch samples at 4Hz (Claim 1). (INCORRECT: Wrong bracket and syntax).
The Apple Watch samples at 8Hz [claim: 3]. (INCORRECT: Not in source text).

SOURCE DOCUMENTS:
{sources}
"""


class PromptCompiler:
    """Builds LLM-ready messages with negative constraints and few-shot citation syntax."""

    def __init__(self, system_prompt: str | None = None) -> None:
        self.system_prompt = system_prompt or _SYSTEM_PROMPT

    def compile(self, directive: str, sources: str) -> list[dict[str, str]]:
        """Return OpenAI-style messages with sources injected into the system envelope."""
        formatted_system = self.system_prompt.format(
            sources=(sources or "").strip() or "None attached."
        )
        return [
            {"role": "system", "content": formatted_system},
            {"role": "user", "content": f"DIRECTIVE: {(directive or '').strip()}"},
        ]

    @staticmethod
    def format_sources_from_rows(sources: list[dict]) -> str:
        """Serialize vault source dicts into the SOURCE DOCUMENTS block."""
        blocks: list[str] = []
        for i, src in enumerate(sources or [], start=1):
            name = str(src.get("name") or src.get("filename") or src.get("id") or f"source-{i}")
            excerpt = str(
                src.get("excerpt") or src.get("extracted_text") or src.get("content") or ""
            ).strip()
            blocks.append(f"--- Document {i}: {name} ---\n{excerpt[:8000]}")
        return "\n\n".join(blocks) if blocks else "None attached."
