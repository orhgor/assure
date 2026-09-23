"""Main document grammar/flow polish with strict lock-pill preservation."""

from __future__ import annotations

import copy
import re
from collections import Counter
from typing import Any

try:
    from ..lib.sanitize import sanitize_jdf_node
    from ..models.jdf import parse_document
except ImportError:
    from lib.sanitize import sanitize_jdf_node
    from models.jdf import parse_document

LOCK_TOKEN_RE = re.compile(r"\[🔒 #\d+\]")

# Every figure a reader would check against the source: optional currency sign,
# digits with thousands separators and decimals, optional percent or magnitude
# suffix. ``$500,000``, ``2.4%``, ``$150M``, ``12`` all match as one token each.
NUMERIC_TOKEN_RE = re.compile(r"[$€£]?\d[\d,]*(?:\.\d+)?(?:%|(?<=\d)[KMBkmb](?![A-Za-z]))?")


def _paragraph_nodes(document: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for section in document.get("body") or []:
        if not isinstance(section, dict):
            continue
        for child in section.get("children") or []:
            if isinstance(child, dict) and child.get("type") == "paragraph":
                nodes.append(child)
    return nodes


def collect_lock_pills(document: dict[str, Any]) -> list[dict[str, Any]]:
    pills: list[dict[str, Any]] = []
    for node in _paragraph_nodes(document):
        meta = node.get("meta") or {}
        for pill in meta.get("lock_pills") or []:
            if not isinstance(pill, dict):
                continue
            lock_hash = str(pill.get("lock_hash") or "").strip()
            if lock_hash:
                pills.append(copy.deepcopy(pill))
    return pills


def _pill_signature(pill: dict[str, Any]) -> tuple[str, str, int]:
    return (
        str(pill.get("lock_hash") or "").strip(),
        str(pill.get("source_id") or "").strip(),
        int(pill.get("lock_index") or 0),
    )


def numeric_tokens(text: str) -> list[str]:
    """The numeric tokens of ``text`` in order (see ``NUMERIC_TOKEN_RE``).

    A trailing separator (``"5,"`` in "5, then") is sentence punctuation the
    regex cannot tell from a thousands separator; stripped so moving a comma
    does not read as changing the figure.
    """
    return [tok.rstrip(",.") for tok in NUMERIC_TOKEN_RE.findall(str(text or ""))]


def numbers_preserved(before_text: str, after_text: str) -> bool:
    """True when every numeric token of ``before_text`` appears, unchanged, in ``after_text``.

    Compared as multisets: a figure may move within the paragraph but may not
    change, split or vanish. The regex polish once turned ``$500,000`` into
    ``$500, 000`` and ``2.4%`` into ``2. 4%`` (2026-09-22 audit); the LLM path can
    round or restate a figure despite the prompt. Either way the paragraph must
    not be returned.
    """
    before = Counter(numeric_tokens(before_text))
    after = Counter(numeric_tokens(after_text))
    return all(after[token] >= count for token, count in before.items())


def strict_preservation_check(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Lock pills and numeric tokens are unchanged across polish.

    Pills are compared by signature over the whole document; numbers are
    compared paragraph by paragraph (matched by node id, by position when ids
    are missing) so a figure cannot migrate between paragraphs and still pass.
    """
    before_pills = collect_lock_pills(before)
    after_pills = collect_lock_pills(after)
    if len(before_pills) != len(after_pills):
        return False
    before_sigs = sorted(_pill_signature(p) for p in before_pills)
    after_sigs = sorted(_pill_signature(p) for p in after_pills)
    if before_sigs != after_sigs:
        return False
    before_nodes = _paragraph_nodes(before)
    after_nodes = _paragraph_nodes(after)
    if len(before_nodes) != len(after_nodes):
        return False
    after_by_id = {str(n.get("id")): n for n in after_nodes if n.get("id")}
    for index, node in enumerate(before_nodes):
        node_id = str(node.get("id") or "")
        counterpart = after_by_id.get(node_id) if node_id else None
        if counterpart is None:
            counterpart = after_nodes[index]
        if not numbers_preserved(str(node.get("content") or ""), str(counterpart.get("content") or "")):
            return False
    return True


def document_plain_text(document: dict[str, Any]) -> str:
    parts: list[str] = []
    for node in _paragraph_nodes(document):
        content = str(node.get("content") or "").strip()
        if content:
            parts.append(content)
    return "\n\n".join(parts)


def _space_after_punctuation(match: re.Match[str]) -> str:
    """``, `` after a comma — except inside a number.

    A separator with a digit on both sides is part of the figure (``500,000``,
    ``2.4``), not sentence punctuation; inserting the space rewrote the amount.
    """
    punct, following = match.group(1), match.group(2)
    start = match.start()
    preceding = match.string[start - 1] if start > 0 else ""
    if preceding.isdigit() and following.isdigit():
        return punct + following
    return f"{punct} {following}"


def _polish_prose(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return cleaned
    cleaned = cleaned[0].upper() + cleaned[1:] if len(cleaned) > 1 else cleaned.upper()
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"([,.;:!?])([^\s])", _space_after_punctuation, cleaned)
    if cleaned[-1] not in ".!?":
        cleaned += "."
    return cleaned


def _polish_with_llm(text: str) -> str | None:
    if not text.strip():
        return text
    try:
        from ..litellm_runner import call_model
    except ImportError:
        from litellm_runner import call_model

    prompt = (
        "Rewrite the following for professional grammar and flow. "
        "Do not remove or alter bracketed lock markers like [🔒 #01]. "
        "Do not change numbers, names, or verified facts.\n\n"
        f"{text}"
    )
    try:
        result = call_model(
            "gemini/gemini-2.0-flash",
            [{"role": "user", "content": prompt}],
            max_tokens=2000,
            intent="analysis",
        )
        polished = str(result or "").strip()
        return polished or None
    except Exception:
        return None


def polish_text_preserving_lock_tokens(text: str, *, use_llm: bool = True) -> str:
    segments: list[str] = []
    cursor = 0
    for match in LOCK_TOKEN_RE.finditer(text):
        chunk = text[cursor : match.start()]
        if chunk:
            polished_chunk = _polish_with_llm(chunk) if use_llm else None
            segments.append(polished_chunk if polished_chunk else _polish_prose(chunk))
        segments.append(match.group(0))
        cursor = match.end()
    tail = text[cursor:]
    if tail:
        polished_tail = _polish_with_llm(tail) if use_llm else None
        segments.append(polished_tail if polished_tail else _polish_prose(tail))
    return "".join(segments).strip()


def polish_document_tree(document: dict[str, Any], *, use_llm: bool = True) -> dict[str, Any]:
    polished = copy.deepcopy(document)
    for node in _paragraph_nodes(polished):
        meta = dict(node.get("meta") or {})
        lock_pills = meta.get("lock_pills") or []
        original_content = str(node.get("content") or "")
        node["content"] = polish_text_preserving_lock_tokens(original_content, use_llm=use_llm)
        meta["lock_pills"] = copy.deepcopy(lock_pills)
        node["meta"] = meta
    return polished


def run_polish_document(document: dict[str, Any], *, use_llm: bool = True) -> dict[str, Any]:
    tree = sanitize_jdf_node(copy.deepcopy(document))
    parse_document(tree)
    before_pills = collect_lock_pills(tree)
    polished = polish_document_tree(tree, use_llm=use_llm)
    polished = sanitize_jdf_node(polished)
    parse_document(polished)
    preserved = strict_preservation_check(tree, polished)
    # A polish that altered a pill or a figure is not offered at all: the caller
    # gets the document it sent, flagged, never the corrupted rewrite.
    delivered = polished if preserved else tree
    return {
        "status": "success" if preserved else "error",
        "document": delivered,
        "plain_before": document_plain_text(tree),
        "plain_after": document_plain_text(delivered),
        "strict_preservation": preserved,
        "preserved": preserved,
        "preservation_note": (
            ""
            if preserved
            else "Polish altered a lock pill or a numeric token; the original text is returned unchanged."
        ),
        "lock_hashes": [str(p.get("lock_hash") or "") for p in before_pills],
        "lock_count": len(before_pills),
    }
