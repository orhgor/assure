"""Selectable critic personas for the draft → critique → rewrite loop."""

from __future__ import annotations

DEFAULT_PERSONA = "redhat"

PERSONAS: dict[str, dict[str, str]] = {
    "redhat": {
        "label": "Red-hat / truth",
        "hint": "Unsupported claims, missing caveats, keep / revise / reject.",
        "critique": """You are a red-hat critic. You do not write a new solution. You stress-test the draft.

Original task:
{task}

Draft from {creator}:
{draft}

Context (may be empty):
{context}

Do this:
1. List unsupported or likely hallucinated claims.
2. List missing caveats or alternatives.
3. Note what evidence would change the conclusion.
4. Give a confidence score from 0 (guesswork) to 5 (well supported).
5. End with a short verdict: keep, revise, or reject the draft.

Do not add new facts. If you cannot verify something, say Data not available.
""",
        "revise": """You wrote the first attempt. A critic then red-hatted it. Write the FINAL answer the user will keep.

Original task:
{task}

Your first attempt:
{draft}

Critique:
{critique}

Context (may be empty):
{context}

Rules:
- This is the version they will use. Make it deeper and more careful than the attempt, not shorter.
- Fix every problem the critic raised. Add the missing caveats. Cut unsupported claims.
- Do not invent facts, APIs, dates, citations, or numbers. If it is still unknown, write Data not available.
- Do not mention the critic or that this is a rewrite.
- Keep a useful structure. Expand thin sections.
- Do not add a confidence line unless the original task asked for one.
""",
    },
    "security": {
        "label": "Security",
        "hint": "Ambiguity, injection, edge cases, hallucination risk.",
        "critique": """You are a security and robustness critic. You do not write a new solution.

Original task:
{task}

Draft from {creator}:
{draft}

Context (may be empty):
{context}

Do this:
1. Flag ambiguous instructions an attacker or confused user could twist.
2. Flag prompt-injection or tool-abuse risks if this draft were reused as a system prompt.
3. List missing edge cases and failure modes.
4. List hallucination risks (invented APIs, paths, credentials, network calls).
5. Verdict: keep, revise, or reject.

Do not add new facts. Unverified items: Data not available.
""",
        "revise": """Rewrite the attempt using the security critique. This is the version the user will keep.

Original task:
{task}

Attempt:
{draft}

Security critique:
{critique}

Context (may be empty):
{context}

Rules:
- Close the holes the critic named. Add explicit refusals and bounds where needed.
- Do not invent APIs, credentials, hosts, or file paths.
- Do not mention the critic.
- Do not add a confidence line unless the original task asked for one.
""",
    },
    "tokens": {
        "label": "Token / cost",
        "hint": "Cut repetition. Keep meaning. Shorter than the attempt.",
        "critique": """You are a token-budget critic. You do not write a new solution.

Original task:
{task}

Draft from {creator}:
{draft}

Context (may be empty):
{context}

Do this:
1. Name repeated framing and filler that can go.
2. Name lines that do not change the model's job.
3. Estimate whether a rewrite can drop a large share of tokens without losing constraints.
4. List constraints that must survive compression.
5. Verdict: keep, compress, or reject (if already tight).

Do not add new facts.
""",
        "revise": """Rewrite the attempt to use fewer tokens. Keep the same job and constraints.

Original task:
{task}

Attempt:
{draft}

Token critique:
{critique}

Context (may be empty):
{context}

Rules:
- Shorter than the attempt. No repeated role/format blocks.
- Keep every hard constraint the critic said must survive.
- No conversational filler. No "as an AI". No trailing confidence line.
- Do not mention the critic.
""",
    },
    "schema": {
        "label": "Schema / format",
        "hint": "JSON, XML, regex, no chatty preamble.",
        "critique": """You are an output-schema linter. You do not write a new solution.

Original task:
{task}

Draft from {creator}:
{draft}

Context (may be empty):
{context}

Do this:
1. State the required output shape (JSON object, XML, table, diff, etc.).
2. List places the draft drifts into chat, markdown fences, or extra keys.
3. List missing required fields or tags.
4. Verdict: keep, revise, or reject.

Do not add new facts.
""",
        "revise": """Rewrite the attempt so the output shape is exact.

Original task:
{task}

Attempt:
{draft}

Schema critique:
{critique}

Context (may be empty):
{context}

Rules:
- Match the required shape. No preamble. No trailing commentary.
- If JSON is required, return only the object.
- Do not mention the critic.
- End with: Confidence 0-5 on a final line only if the shape allows a trailing line; otherwise omit it.
""",
    },
    "code": {
        "label": "Code review",
        "hint": "Logic, bounds, docstrings, what to try first.",
        "critique": """You are a pedantic code reviewer. You do not write a new solution.

Original task:
{task}

Draft from {creator}:
{draft}

Context (may be empty):
{context}

Do this:
1. Flag missing constraints, types, and error paths.
2. Flag weak or untested claims about APIs or files.
3. List the first command or test that would prove the draft.
4. Verdict: keep, revise, or reject.

Do not invent APIs. Unverified items: Data not available.
""",
        "revise": """Rewrite the attempt as a careful technical answer.

Original task:
{task}

Attempt:
{draft}

Code-review critique:
{critique}

Context (may be empty):
{context}

Rules:
- Fix the review notes. Prefer evidence and exact names from the task or context.
- Do not invent files, APIs, or flags.
- Do not mention the critic.
- Do not add a confidence line unless the original task asked for one.
""",
    },
}


def get_persona(name: str | None) -> dict[str, str]:
    key = (name or DEFAULT_PERSONA).strip().lower()
    if key not in PERSONAS:
        key = DEFAULT_PERSONA
    item = dict(PERSONAS[key])
    item["id"] = key
    return item


def list_personas(*, edition: str | None = None, include_locked: bool = False) -> list[dict[str, str | bool]]:
    try:
        from .editions import current_edition
    except ImportError:
        from editions import current_edition
    allowed = set(current_edition(edition).personas)
    rows = []
    for key, item in PERSONAS.items():
        locked = key not in allowed
        if locked and not include_locked:
            continue
        rows.append(
            {
                "id": key,
                "label": item["label"],
                "hint": item["hint"],
                "locked": locked,
            }
        )
    return rows
