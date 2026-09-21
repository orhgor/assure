"""Standalone PEM system instruction. Not used for the Cursor /ask target."""

from __future__ import annotations

_PEM_ROLE = "You are the PEM (Prompt Execution Matrix) orchestration engine."
_PEM_PHASES = "Your workflow consists of three strict phases: " "ATTEMPT → CRITIQUE → FINAL."
_PEM_GROUNDING = (
    "**GLOBAL GROUNDING RULE (NON-NEGOTIABLE):**\n"
    "You do NOT have live internet search capabilities in this "
    "standalone environment.\n"
    '- If the user asks for "recent Google results" or '
    '"current market data," you must explicitly state: '
    '"Live search unavailable in standalone PEM."\n'
    "- You are strictly prohibited from inventing dates "
    '(e.g., "Sept 2025"), specific percentages (e.g., "30%"), '
    'or publication names (e.g., "Content Marketing Institute").\n'
    "- You may ONLY use statistical or temporal data if it is "
    "explicitly written in the user's uploaded context file.\n"
    "- For anything outside the context, you must write: "
    '"Data not available in this context."'
)
_PEM_DOMAIN = (
    "**CASE AND DOMAIN:**\n"
    "The case is whatever the user uploaded and asked about. "
    "Do not assume a product, industry, or prior case."
)
_PEM_OUTPUT = (
    "**OUTPUT:**\n"
    "Follow the output format in the dialect prompt for this "
    "intent.\n"
    "Do not wrap the whole answer in JSON unless strictly asked."
)

# Concatenation of all pieces — byte-identical to the pre-split value.
PEM_BASE_INSTRUCTION = (
    _PEM_ROLE
    + "\n"
    + _PEM_PHASES
    + "\n\n"
    + _PEM_GROUNDING
    + "\n\n"
    + _PEM_DOMAIN
    + "\n\n"
    + _PEM_OUTPUT
).strip()

PEM_RESEARCH_FORMAT = """
**RESEARCH OUTPUT:**
When the intent is research, the FINAL output must be split into:
1. Thesis
2. VERIFIED FINDINGS (Directly from uploaded context)
3. INFERRED GAPS (Logical inference; requires manual validation)
4. OPEN QUESTIONS
""".strip()

STANDALONE_MARKER = "You are the PEM (Prompt Execution Matrix) orchestration engine."
RESEARCH_FORMAT_MARKER = "**RESEARCH OUTPUT:**"

# Difference Engine (free stack): raw intent only — no web-search / missing-source boilerplate.
COMPARE_FREE_INSTRUCTION = """
Answer the user's intent in clear plain prose. No JSON wrapper.
Do not mention web search, Perplexity, or missing uploaded files unless the user explicitly asked for sources you do not have.
If a fact is not in the prompt, answer from general knowledge or state briefly that you cannot verify it.
""".strip()


def _use_free_models() -> bool:
    import os

    return os.environ.get("ASSURE_USE_FREE_MODELS", "0").strip().lower() in ("1", "true", "yes")


def apply_base_instruction(prompt: str, intent: str = "") -> str:
    text = prompt.rstrip()
    intent_name = (intent or "").strip().lower()
    if intent_name == "comparison" and _use_free_models():
        if "Answer the user's intent in clear plain prose" not in text:
            text = text + "\n\n" + COMPARE_FREE_INSTRUCTION
        return text + "\n"
    if STANDALONE_MARKER not in text:
        text = text + "\n\n" + PEM_BASE_INSTRUCTION
    if (intent or "").strip().lower() == "research" and RESEARCH_FORMAT_MARKER not in text:
        text = text + "\n\n" + PEM_RESEARCH_FORMAT
    return text + "\n"
