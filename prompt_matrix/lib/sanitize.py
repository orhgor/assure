"""Strict HTML sanitization for JDF node strings before SQLite writes."""

from __future__ import annotations

import re
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


#: A bare ``&`` — one that does not open a character reference — in document text.
#: ``[A-Za-z][A-Za-z0-9]{1,31};`` is the named-entity shape, ``#123;`` and
#: ``#x1f;`` the numeric ones.
_BARE_AMPERSAND = re.compile(
    r"&(?!(?:[A-Za-z][A-Za-z0-9]{1,31}|#\d{1,7}|#[xX][0-9A-Fa-f]{1,6});)"
)

#: A ``<`` that does not open a tag. html5lib — and so ``bleach`` — reads ``<``
#: followed by a letter, ``!``, ``/`` or ``?`` as markup and any other ``<`` as
#: character data, so this protects exactly the ones the parser treats as text and
#: never hides markup from the filter.
_BARE_LESS_THAN = re.compile(r"<(?![A-Za-z!/?])")

#: What a protected character is replaced with while bleach runs, restored after.
#: A private-use code point cannot open or close a tag and bleach leaves it alone.
_AMP_SENTINEL = "\ue000"
_LT_SENTINEL = "\ue001"


def _clean_html(value: str) -> str:
    """Strip dangerous markup; leave text that carries no markup byte-identical.

    ``bleach.clean`` is an HTML *writer* as well as a filter: it escapes every bare
    ``&`` and every ``<`` into an entity, in strings that carry no tag at all.
    Applied to a document's own text, that escape never round-trips — a real
    renewal memo's "Princeton Excess & Surplus Lines Insurance Company" was written
    to SQLite, served, and exported as ``&amp;``, which an underwriter reads as a
    mangled company name and which makes an exported JDF hash differently from the
    tree a re-import of it produces. A sub-limit line ("$500,000 x/s") and a
    comparison ("Premium < $1,000,000") are the same shape. Text with no ``<``
    cannot carry markup, so it is returned unchanged.

    Text that does go through bleach has its *bare* ampersands and non-tag ``<``
    protected first and restored afterwards, which is the only version of this that
    is both an identity for the text and safe to hand to a browser.
    ``html.unescape`` was tried here and was wrong: bleach leaves an entity the
    document already carried as an entity, so unescaping its output put markup back
    — measured, ``&lt;script&gt;alert(1)&lt;/script&gt;`` was stored as a live
    ``<script>``, and ``&lt;img src=x onerror=alert(1)&gt;`` as a live element with
    its handler attached. The guard was also not idempotent: a second pass stripped
    the tags the first pass had created, so an export and a re-import of it could
    never agree. Escaped markup now stays escaped and an entity stays the entity the
    document wrote.

    One asymmetry is deliberate: a bare ``>`` in text is left to bleach, which
    emits ``&gt;``. It renders identically, and because the escaped form carries no
    ``<`` the second pass is an identity — so it is stable, which is what the export
    hash needs.
    """
    if "<" not in value:
        return value
    try:
        import bleach
    except ImportError:
        return _fallback_strip(value)
    sentinels = [
        (sentinel, pattern)
        for sentinel, pattern in ((_AMP_SENTINEL, _BARE_AMPERSAND), (_LT_SENTINEL, _BARE_LESS_THAN))
        if sentinel not in value
    ]
    for sentinel, pattern in sentinels:
        value = pattern.sub(sentinel, value)
    cleaned = bleach.clean(
        value,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        strip=True,
        strip_comments=True,
    )
    for sentinel, _pattern in sentinels:
        cleaned = cleaned.replace(sentinel, "&" if sentinel == _AMP_SENTINEL else "<")
    return cleaned


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
