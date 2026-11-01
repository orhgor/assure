"""Public-copy gates from the site revision spec, section 7."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

PAGES = {
    "/": DIST / "index.html",
    "/about": DIST / "about" / "index.html",
    "/architecture": DIST / "architecture" / "index.html",
    "/security": DIST / "security" / "index.html",
    "/privacy": DIST / "privacy" / "index.html",
    "/terms": DIST / "terms" / "index.html",
    "/solutions": DIST / "solutions" / "index.html",
    "/contact-us": DIST / "contact-us" / "index.html",
}

BANNED = re.compile(
    r"\b(on this machine|on this computer|filter, not a guarantee|solo|journalist|"
    r"Z3 SMT ENGINE|v4\.12|JDF v2|PEM engine|openrouter)\b",
    re.I,
)
BRANDS = re.compile(
    r"\b(Parsure|ParSure|CrossSure|DraftSure|Substrate Vault|Truth Ledger|Red-Hat|Laya|JDF)\b",
    re.I,
)
EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\u2600-\u27BF]"
)
EXAMPLE = ("$1,420,000", "$1,450,000", "Section 1.2", "p. 14")


def _pages():
    missing = [str(p) for p in PAGES.values() if not p.is_file()]
    if missing:
        pytest.skip("marketing dist is not built: " + ", ".join(missing))
    return {name: path.read_text(encoding="utf-8") for name, path in PAGES.items()}


def test_banned_strings_absent():
    for name, html in _pages().items():
        assert not BANNED.search(html), name


def test_sub_brand_names_only_on_architecture():
    for name, html in _pages().items():
        if BRANDS.search(html) and name != "/architecture":
            raise AssertionError(name)


def test_no_emoji_in_rendered_text():
    for name, html in _pages().items():
        text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
        text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
        assert not EMOJI.search(text), name


def test_canonical_example_and_no_table_4():
    pages = _pages()
    for name in ("/", "/solutions"):
        html = pages[name]
        for bit in EXAMPLE:
            assert bit in html, (name, bit)
        assert "Table 4" not in html
    for html in pages.values():
        assert "Table 4" not in html
        assert "start_char" not in html
        assert "end_char" not in html
        assert "node_id" not in html


def test_no_unresolved_confirm_markers():
    for name, html in _pages().items():
        assert "[CONFIRM]" not in html, name
