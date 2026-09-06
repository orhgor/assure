"""Strict HTML sanitization for JDF node strings before SQLite writes."""

from __future__ import annotations

from typing import Any

ALLOWED_TAGS = [
    "p",
    "span",
    "br",
    "strong",
    "em",
    "u",
    "a",
    "ul",
    "ol",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "table",
    "tr",
    "td",
    "th",
    "img",
]
ALLOWED_ATTRS = {
    "a": ["href", "title", "rel"],
    "img": ["src", "alt", "title"],
    "span": ["class"],
    "*": ["class"],
}
STRIP_TAGS = {"script", "iframe", "object", "embed", "form", "input", "textarea"}
_TEXT_KEYS = frozenset(
    {"content", "title", "html", "caption", "alt", "text", "body", "change_summary"}
)


def _clean_html(value: str) -> str:
    try:
        import bleach
    except ImportError:
        return _fallback_strip(value)
    return bleach.clean(
        value,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        strip=True,
        strip_comments=True,
    )


def _fallback_strip(value: str) -> str:
    import re

    text = value
    for tag in STRIP_TAGS:
        text = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", "", text, flags=re.I | re.S)
        text = re.sub(rf"<{tag}[^>]*/?>", "", text, flags=re.I)
    text = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", text, flags=re.I)
    return text


def sanitize_jdf_node(node: Any) -> Any:
    """Recursively strip dangerous markup from a JDF node or document tree."""
    if isinstance(node, str):
        return _clean_html(node)
    if isinstance(node, list):
        return [sanitize_jdf_node(item) for item in node]
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, val in node.items():
            if isinstance(val, str) and (key in _TEXT_KEYS or "<" in val):
                out[key] = _clean_html(val)
            else:
                out[key] = sanitize_jdf_node(val)
        return out
    return node
