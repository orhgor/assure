"""Answer shape — the output follows the ask; the verification contract does not.

The compile prompt fixed one shape for every ask: "Draft clear, structured prose
for a business document. Use markdown headings (## Section) for major sections"
(``routers/draft._DRAFT_SYSTEM``). So a one-line extraction question — "What is the
deductible for Suffolk?" — came back as a memo: headings, a preamble, sections
built to fill a page, and the answer buried in the first paragraph. The reader
asked for a figure and was handed a document.

Two shapes, decided from the ask alone. The decision is deterministic and calls no
model, so the shape is a pure function of the ask — and the compile cache key
already contains the ask (``_compile_source_text``), so a cached compile cannot
serve a shape its ask did not earn.

**Output shape may change the length, never the claim unit.** The paragraph node
is what the anchoring gate measures and what the entailment check judges; splitting
a sentence out of its paragraph changes what the product claims about a claim that
nothing else changed. That is why both shapes below emit the same unit at two
lengths, and why the next person to "improve" a short answer should not reach for
splitting it again.

``direct``
    The ask is a question about a fact the source states, or an extraction ask.
    The answer is the claim units themselves: one paragraph node per claim, no
    heading, no preamble.

``memo``
    The ask wants a document (summarize, memo, report, draft, write, compare,
    audit). Structured prose under markdown headings, as before.

Neither shape changes what verification attaches to. A ``direct`` document is
built as the claim unit the engine counts — one paragraph node for the answer —
because a sentence-sized node carries too few content tokens to reach the anchor
matcher's overlap floor, and a ``direct`` shape that grounded *less* than the memo
it replaces would be a regression, not a fix (see ``direct_sections`` for the
measurement). Every claim in either shape carries the same state the pipeline
derives for it (``supported`` / ``partial`` / ``unsupported`` / ``unverified`` /
``anchored`` / ``unanchored``), and the export sidecar reports them per node.
"""

from __future__ import annotations

import re
from typing import Any

try:
    from ..models.jdf import (
        JDFDocumentTree,
        _merge_short_sentences,
        _split_sentences,
        _strip_inline_markdown,
        empty_annotations,
        new_node_id,
    )
    from .fast_router import classify_intent
except ImportError:
    from models.jdf import (
        JDFDocumentTree,
        _merge_short_sentences,
        _split_sentences,
        _strip_inline_markdown,
        empty_annotations,
        new_node_id,
    )
    from fast_router import classify_intent

DIRECT = "direct"
MEMO = "memo"

#: An ask that wants a document, not an answer. Matched on whole words: "brief"
#: is a deliverable, "briefly" is not, so the token sets stay separate.
#:
#: The nouns of the *material* are deliberately absent — "policy", "document" and
#: "paper" name what the source often is, so cues like them read "Extract the
#: obligations from the policy" as an ask for a policy-shaped document. An ask that
#: wants one of those written still carries its verb ("write a policy"), and the
#: verb is a cue.
_MEMO_CUES = frozenset(
    {
        "draft",
        "write",
        "summarize",
        "summarise",
        "summary",
        "compose",
        "narrative",
        "report",
        "memo",
        "memorandum",
        "brief",
        "briefing",
        "overview",
        "update",
        "outline",
        "proposal",
        "plan",
        "analysis",
        "analyze",
        "analyse",
        "assess",
        "assessment",
        "dossier",
        "deliverable",
        "rewrite",
        "expand",
        "elaborate",
    }
)

#: The opening words that make an ask a question. Same convention as
#: ``services/compile_guard._INTERROGATIVE_OPEN`` — a document that opens by
#: asking rather than asserting.
_INTERROGATIVE = frozenset(
    {
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "when",
        "where",
        "why",
        "how",
        "does",
        "do",
        "did",
        "is",
        "are",
        "was",
        "were",
        "can",
        "could",
        "should",
        "would",
        "will",
        "may",
        "might",
        "has",
        "have",
    }
)

_WORDS = re.compile(r"[a-z]+")

_DIRECT_INSTRUCTION = (
    "## Answer shape (this ask wants an answer)\n"
    "Answer the question itself in one to three plain sentences. No heading, no "
    "preamble, no memo, no section list — the reader asked for a fact, and a "
    "document is not an answer. State only the fact the ask is about, and keep "
    "each figure, name and date with the sentence that states it: a second figure "
    "or date from another part of the source belongs in a memo, not in the answer. "
    "Do not pad the answer to look like a report."
)

_MEMO_INSTRUCTION = (
    "## Answer shape (this ask wants a document)\n"
    "Write it as a structured memo: a markdown heading (## Section) for each "
    "major section, one claim per sentence, and no preamble about what you are "
    "about to do. Every claim in the memo is held to the source exactly as it "
    "would be in a short answer."
)

_INSTRUCTIONS = {DIRECT: _DIRECT_INSTRUCTION, MEMO: _MEMO_INSTRUCTION}

#: Title of the single section a direct answer lives under. The canvas renders
#: section titles as document headings, so a nameless one would read as prose.
_DIRECT_SECTION_TITLE = "Answer"


def choose_shape(intent: str) -> str:
    """``direct`` for a question or an extraction ask, ``memo`` for a document ask."""
    text = (intent or "").strip()
    if not text:
        return MEMO
    if set(_WORDS.findall(text.lower())) & _MEMO_CUES:
        return MEMO
    if classify_intent(text) == "extract":
        return DIRECT
    if looks_like_question(text):
        return DIRECT
    return MEMO


def looks_like_question(text: str) -> bool:
    """A trailing question mark, or an interrogative as the ask's first word."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    if stripped.endswith("?"):
        return True
    words = _WORDS.findall(stripped.lower())
    return bool(words) and words[0] in _INTERROGATIVE


def shape_instruction(shape: str) -> str:
    """The system-prompt block that fixes the shape for this ask."""
    return _INSTRUCTIONS.get(shape, _INSTRUCTIONS[MEMO])


def direct_sections(text: str) -> list[dict[str, Any]]:
    """A direct answer as ONE paragraph node under one section — the claim unit.

    The answer is 1–3 sentences; it is deliberately not split into one node per
    sentence. A sentence-sized node carries too few content tokens to reach the
    anchor matcher's overlap floor against a source sentence of ordinary length,
    so splitting *loses* the grounding the answer has: measured on the NAIC demo
    source, "What is the deductible for Suffolk?" anchored as one paragraph
    (1 of 1 claims, partial) and anchored 0 of 1 as separate sentences — the gate
    then refused the whole compile as ungrounded. The floors
    (``models.jdf._MIN_ANCHOR_OVERLAP`` / ``_MIN_ANCHOR_COEFFICIENT``) are not this
    module's to move, so the direct shape keeps the claim unit the engine counts
    and carries one verification state for the answer, exactly as a memo
    paragraph carries one for its own claim.

    Headings are dropped (the shape has none, and a stray one would read as a
    claim), bullet markers are stripped, and the remaining sentences are joined.
    """
    body = "\n".join(
        line for line in str(text or "").splitlines() if not line.strip().startswith("#")
    )
    body = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", body, flags=re.MULTILINE)
    units = [sent for sent, _page in _merge_short_sentences(_split_sentences(body)) if sent.strip()]
    # `_split_sentences` drops the terminator, so the join puts it back: an answer
    # that reads "…insured value It applies per occurrence" is not prose.
    joined = ". ".join(units)
    if joined and not joined.endswith((".", "!", "?", ":", ";")):
        joined += "."
    content = _strip_inline_markdown(joined)
    if not content:
        return []
    return [
        {
            "type": "section",
            "id": new_node_id("sec"),
            "title": _DIRECT_SECTION_TITLE,
            "children": [
                {
                    "type": "paragraph",
                    "id": new_node_id("para"),
                    "content": content,
                    "entities_referenced": [],
                    "meta": {"source": "generate_draft"},
                    "annotations": empty_annotations(),
                }
            ],
            "meta": {"source": "generate_draft"},
            "annotations": empty_annotations(),
        }
    ]


def build_direct_document(
    project_id: str,
    draft_text: str,
    *,
    truth_ledger: dict[str, float | str | int] | None = None,
) -> JDFDocumentTree:
    """Parse a direct answer into a ``JDFDocumentTree`` — the ``memo`` path's twin."""
    body = direct_sections(draft_text)
    if not body:
        raise ValueError("direct answer carried no claim units")
    return JDFDocumentTree(
        document_id=f"doc-{project_id}",
        meta={"project_id": project_id, "source": "generate_draft"},
        truth_ledger=truth_ledger or {},
        body=body,  # type: ignore[arg-type]
    )
