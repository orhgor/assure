"""Word-level diff for surgical revisions.

The model returns a replacement string; the backend computes what changed. Two
properties make the result readable rather than alarming.

Normalization runs before the comparison. A model that returns curly quotes, an
en-dash for a hyphen, or a single space where the original had two produces a
character-level diff that deletes and reinserts the entire paragraph — the diff
is technically correct and useless to a reader, who sees a wall of red and green
around changes nobody made. Quotes, dashes and whitespace runs are therefore
folded for comparison only; the text that reaches the document is the model's
own output, not the normalized form.

The unit is the word, not the character. Word-level output keeps a changed
figure adjacent to the words around it, so a reader sees `5,000,000` replaced by
`7,000,000` rather than the shared digits the two figures have in common.
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Literal, Tuple

DiffOp = Literal["equal", "insert", "delete"]

#: Folded for comparison only. Order matters: NBSP becomes a space before runs
#: are collapsed, so a stray NBSP does not survive as a distinct token.
_QUOTE_MAP = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u00a0": " ",
        "\u202f": " ",
        "\u2009": " ",
    }
)

_WHITESPACE_RUN = re.compile(r"\s+")

#: A token is a run of non-space characters. Punctuation stays attached, so
#: "25,000." and "25,000" differ by one trailing token rather than by a word
#: boundary the reader would not see.
_TOKEN_RE = re.compile(r"\S+")


def normalize_for_diff(text: str) -> str:
    """The comparison form: folded quotes and dashes, collapsed whitespace.

    Never returned to a caller as document text. Its only consumer is the diff,
    which needs two strings that differ where the *content* differs.
    """
    folded = (text or "").translate(_QUOTE_MAP)
    return _WHITESPACE_RUN.sub(" ", folded).strip()


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


def compute_word_diff(original: str, proposed: str) -> list[dict[str, Any]]:
    """A word-level diff as ``[{op, text}, …]`` in original order.

    Adjacent ops of the same kind are merged, so a reader sees one run of
    deletion rather than one entry per word. ``equal`` segments are included —
    a caller rendering only the changes cannot place them without the text
    between them.
    """
    a = _tokens(normalize_for_diff(original))
    b = _tokens(normalize_for_diff(proposed))

    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    ops: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            ops.append({"op": "equal", "text": " ".join(a[i1:i2])})
        elif tag == "delete":
            ops.append({"op": "delete", "text": " ".join(a[i1:i2])})
        elif tag == "insert":
            ops.append({"op": "insert", "text": " ".join(b[j1:j2])})
        elif tag == "replace":
            # A replacement renders as a deletion followed by an insertion, so
            # the UI has one shape to handle and the reader sees both sides.
            ops.append({"op": "delete", "text": " ".join(a[i1:i2])})
            ops.append({"op": "insert", "text": " ".join(b[j1:j2])})
    return [op for op in ops if op["text"] or op["op"] != "equal"]


def diff_summary(ops: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts a caller can branch on without walking the list."""
    deleted = sum(len(o["text"].split()) for o in ops if o["op"] == "delete")
    inserted = sum(len(o["text"].split()) for o in ops if o["op"] == "insert")
    return {
        "deleted_words": deleted,
        "inserted_words": inserted,
        "changed": bool(deleted or inserted),
    }


# ── edge whitespace ──────────────────────────────────────────────────────────


def split_edge_whitespace(text: str) -> tuple[str, str, str]:
    """``(leading, body, trailing)`` — the whitespace a replacement must keep.

    A model handed ``"The patient recovered. "`` returns
    ``"The patient fully recovered."`` without the trailing space, and a
    wholesale replace erases it. Repeated across edits the spaces between
    paragraphs disappear and sentences run together. The edges belong to the
    document's layout, not to the sentence the model was asked to rewrite, so
    they are captured from the original and re-applied to the result.
    """
    raw = text or ""
    stripped = raw.strip()
    if not stripped:
        return raw, "", ""
    start = len(raw) - len(raw.lstrip())
    end = len(raw) - len(raw.rstrip())
    leading = raw[:start]
    trailing = raw[len(raw) - end :] if end else ""
    body = raw[start : len(raw) - end] if end else raw[start:]
    return leading, body, trailing


def restore_edge_whitespace(original: str, proposed: str) -> str:
    """``proposed`` with the original's leading and trailing whitespace."""
    leading, _body, trailing = split_edge_whitespace(original)
    return f"{leading}{(proposed or '').strip()}{trailing}"


# ── format drift ─────────────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s", re.MULTILINE)
_LIST_RE = re.compile(r"^\s{0,3}(?:[-*+]|\d+[.)])\s", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*[^*]+\*\*")
_STRUCTURE_WORDS = (
    "heading",
    "header",
    "bullet",
    "list",
    "bold",
    "italic",
    "numbered",
    "section",
    "format",
    "formatting",
    "markdown",
)


def structure_counts(text: str) -> dict[str, int]:
    """Markdown structures a surgical edit must not introduce."""
    body = text or ""
    return {
        "headings": len(_HEADING_RE.findall(body)),
        "lists": len(_LIST_RE.findall(body)),
        "bold": len(_BOLD_RE.findall(body)),
    }


def instruction_asks_for_structure(instruction: str) -> bool:
    """True when the user asked for formatting, so a change is not drift."""
    low = (instruction or "").lower()
    return any(word in low for word in _STRUCTURE_WORDS)


def format_drift(
    original: str, proposed: str, instruction: str
) -> tuple[bool, list[str]]:
    """``(drifted, reasons)`` — structure added or removed without being asked.

    The prompt forbids added headers and bullets; a prompt rule is a request,
    and this is the check. Deterministic, so the model cannot argue its way past
    it and a reviewer can reproduce the verdict from the two strings.
    """
    if instruction_asks_for_structure(instruction):
        return False, []
    before = structure_counts(original)
    after = structure_counts(proposed)
    reasons: list[str] = []
    for kind in ("headings", "lists", "bold"):
        if before[kind] != after[kind]:
            direction = "added" if after[kind] > before[kind] else "removed"
            reasons.append(
                f"{direction} {kind} ({before[kind]} -> {after[kind]}) without being asked"
            )
    return bool(reasons), reasons


# ── the surgical prompt ──────────────────────────────────────────────────────

SURGICAL_SYSTEM_PROMPT = """You are an expert surgical editor for a high-stakes document system.
Your task is to revise a single, isolated text node based strictly on the user's instruction.

# CORE RULES
1. MINIMAL INTERVENTION: Change ONLY what is required to satisfy the user's instruction. Preserve the original sentence structure, tone, and vocabulary as much as possible.
2. ZERO DRIFT: Do not add introductory or concluding remarks. Do not summarize.
3. STRICT GROUNDING: You must not invent new facts, figures, qualifiers, or claims. The revised text must remain fully supported by the original context.
4. FORMAT PRESERVATION: Do not add Markdown headers, bullet points, or bolding unless the user explicitly requested structural changes.

# FORBIDDEN BEHAVIORS
- Never output conversational filler (e.g., "Here is the updated text:").
- Never expand the scope beyond the specific node provided.
- Never "smooth out" or rewrite surrounding concepts that the user did not ask to change.

The preceding and following text is provided so you can resolve pronouns such as
"it" or "the policy". You may read it. Your output MUST replace only the target
text and nothing else."""


def build_surgical_prompt(
    *,
    original_text: str,
    user_instruction: str,
    preceding: str = "",
    following: str = "",
    source_context: str = "",
) -> list[dict[str, str]]:
    """The surgical-edit messages, with the target fenced and the context read-only.

    The fence markers are the same shape the compile path uses for untrusted
    material, so the model has one convention to learn: text inside a named
    block is content, not instruction.
    """
    parts = [
        f"[ORIGINAL_TEXT]\n{original_text}\n[/ORIGINAL_TEXT]",
        f"[USER_INSTRUCTION]\n{user_instruction}\n[/USER_INSTRUCTION]",
    ]
    if preceding:
        parts.append(f"[PRECEDING_NODE_TEXT]\n{preceding}\n[/PRECEDING_NODE_TEXT]")
    if following:
        parts.append(f"[FOLLOWING_NODE_TEXT]\n{following}\n[/FOLLOWING_NODE_TEXT]")
    if source_context:
        parts.append(f"[SOURCE_CONTEXT]\n{source_context}\n[/SOURCE_CONTEXT]")
    parts.append(
        "Analyze the request, write your constraint-checking rationale, and output "
        "the exact proposed_text to replace the original."
    )
    return [
        {"role": "system", "content": SURGICAL_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


SURGICAL_RESPONSE_SCHEMA: dict[str, Any] = {
    "name": "SurgicalNodePatch",
    "description": "Generates a minimal, node-scoped text replacement based on user instructions.",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "rationale": {
                "type": "string",
                "description": (
                    "Step-by-step reasoning confirming that the edit strictly addresses "
                    "the user instruction without expanding scope, inventing facts, or "
                    "altering formatting."
                ),
            },
            "proposed_text": {
                "type": "string",
                "description": (
                    "The exact replacement text for the node. Must contain NO "
                    "conversational filler."
                ),
            },
        },
        "required": ["rationale", "proposed_text"],
        "additionalProperties": False,
    },
}


def parse_surgical_response(raw: Any) -> Tuple[str, str]:
    """``(proposed_text, rationale)`` from a model response, or raise.

    The schema is enforced here rather than trusted to the provider: some models
    accept ``response_format`` and ignore it, and a model that answers with prose
    must not reach the document.

    A string that *is* a JSON object is parsed, because that is what a model
    honouring the schema returns through a transport that hands back a string —
    treating it as the proposal would write ``{"rationale": …, "proposed_text": …}``
    into the document verbatim. A string that is not JSON is the proposal itself:
    that is the shape the previous prompt produced, and refusing it would break
    every model that ignores the schema without improving the result.
    """
    payload = _parse_json_object(raw)
    if payload is None:
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                raise ValueError("model returned an empty proposal")
            return text, ""
        raise ValueError(f"model returned {type(raw).__name__}, expected an object")

    extra = set(payload) - {"rationale", "proposed_text"}
    if extra:
        raise ValueError(f"model returned unexpected keys: {sorted(extra)}")

    proposed = payload.get("proposed_text")
    if not isinstance(proposed, str) or not proposed.strip():
        raise ValueError("model returned no proposed_text")
    return proposed, str(payload.get("rationale") or "")


def _parse_json_object(raw: Any) -> dict[str, Any] | None:
    """``raw`` as a JSON object, or None when it is not one.

    Handles the fenced form models emit (````json … ````) as well as bare JSON.
    """
    import json as _json

    candidate: Any = raw
    if isinstance(candidate, str):
        text = candidate.strip()
        if text.startswith("```"):
            text = "\n".join(
                line for line in text.splitlines() if not line.strip().startswith("```")
            ).strip()
        if not text.startswith("{"):
            return None
        try:
            candidate = _json.loads(text)
        except Exception:
            return None
    return candidate if isinstance(candidate, dict) else None
