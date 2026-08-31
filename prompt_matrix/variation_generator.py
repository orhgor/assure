"""Format variations for an intent.

Default path is local seeds. It does not call a model per question.
PEM_GENERATE_VARIATIONS=1 can ask a live model once to add extra format
strings for an intent that still has only seeds. That is an explicit opt-in
Send, not a hidden fan-out of 3-5 calls on every Get my answer.
"""

from __future__ import annotations

import os
import re

# Structural alternatives. The first slot is filled with config.json's
# current output_format when seeds are written.
SEED_EXTRAS: dict[str, list[str]] = {
    "research": [
        "Open with one thesis sentence. Then a bullet list of claims that appear in the files. Then a second list of inferences labeled as inference. Then open questions. No invented dates, percents, or publication names.",
        "Use a two-column layout in prose: Claim. Then Source (file line or 'not in files'). End with what is still unknown.",
        "Write a short narrative, then a numbered list of gaps. If a fact is missing from the files, write exactly: Data not available.",
    ],
    "design": [
        "Cover goals, constraints, the proposed design, tradeoffs, and one concrete next step.",
        "Start with constraints, then the design, then what you would cut first if time is short.",
        "List options as a table of approach, cost of delay, and revert plan. Then pick one.",
    ],
    "comparison": [
        "Use a comparison table, then a recommendation, then the conditions that would change it.",
        "Lead with the recommendation in one sentence. Then a table. Then the risks of that pick.",
        "Score each option on the same five criteria. State the winner last, not first.",
    ],
    "debug": [
        "List hypotheses ranked by likelihood, how to confirm each, and the first command or code change to try.",
        "State the failing symptom, then the smallest reproducing step, then one patch to try.",
        "Write a numbered timeline of what you would check. Put the first command at the top.",
    ],
    "analysis": [
        "State the method, the results, the limitations, and what the numbers do not prove.",
        "Lead with the result in one sentence. Then method. Then what would falsify it.",
        "Show a compact results list, then caveats, then a sentence on sample size or missing inputs.",
    ],
}


def infer_domain(task: str, context: str = "") -> str:
    blob = f"{task}\n{context}".lower()
    if any(token in blob for token in ("traceback", "stack trace", "function", "typescript", "python", "compile error")):
        return "coding"
    if any(token in blob for token in ("campaign", "landing page", "brand voice", "cta")):
        return "marketing"
    if any(token in blob for token in ("paper", "citation", "literature", "hypothesis")):
        return "research"
    return "general"


def generate_with_model_enabled() -> bool:
    env = os.environ.get("PEM_GENERATE_VARIATIONS", "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    if env in {"0", "false", "off", "no"}:
        return False
    try:
        from .engine import load_matrix
    except ImportError:
        from engine import load_matrix
    data = load_matrix().runtime.model_dump()
    block = data.get("improve") if isinstance(data.get("improve"), dict) else {}
    return bool(block.get("generate_with_model", False))


def seed_texts(intent: str, base_format: str) -> list[str]:
    extras = list(SEED_EXTRAS.get(intent, []))
    texts = [base_format.strip()] if base_format.strip() else []
    for item in extras:
        if item.strip() and item.strip() not in texts:
            texts.append(item.strip())
    return texts[:5]


def parse_numbered_formats(blob: str) -> list[str]:
    parts = re.split(r"(?m)^\s*\d+[.)]\s+", blob or "")
    out = []
    for part in parts:
        text = " ".join(part.strip().split())
        if len(text) >= 40:
            out.append(text[:800])
    return out[:5]


def maybe_ask_model_for_formats(intent: str, base_format: str, target_ai: str) -> list[str]:
    """Optional. One Send. Off unless PEM_GENERATE_VARIATIONS=1."""
    if not generate_with_model_enabled():
        return []
    prompt = (
        f"Write four alternative output-format instructions for a {intent} task.\n"
        f"The current instruction is:\n{base_format}\n\n"
        "Return only a numbered list. Each item must be one instruction, no examples, "
        "no invented statistics."
    )
    try:
        from .pipelines import _ask
    except ImportError:
        from pipelines import _ask
    try:
        reply, err, _note, _used = _ask(target_ai or "gemini", prompt, failover=True, intent=intent)
    except Exception:
        return []
    if err or not reply:
        return []
    return parse_numbered_formats(reply)
