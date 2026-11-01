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
    r"on this machine|on this computer|stays on your computer|keys never leave your machine|"
    r"filter, not a guarantee|No contradiction survives|Z3 SMT ENGINE|v4\.12|JDF v2|"
    r"PEM engine|openrouter|The Deterministic Truth Engine|Paste a paragraph on the landing page|"
    r"Solo High-Stakes Creators|Journalists & Fact-Checkers",
    re.I,
)
BAD_CASE = re.compile(r"Parsure|Par Sure|Cross Sure|Draft Sure|Crosssure|Draftsure")
LEAKS = ("substrate_vault", "jdf_revisions", "user_activity_log", "audit_log", "groundrails", "start_char", "end_char", "node_id")
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")
EXAMPLE = ("$1,420,000", "$1,450,000", "$30,000", "p. 14", "row 212", "Section 1.2")
LOGOS = ("onprem-icon.svg", "aws-startups.svg", "s3-icon.svg")


def _pages():
    missing = [str(p) for p in PAGES.values() if not p.is_file()]
    if missing:
        pytest.skip("marketing dist is not built: " + ", ".join(missing))
    return {name: path.read_text(encoding="utf-8") for name, path in PAGES.items()}


def test_banned_strings_absent():
    for name, html in _pages().items():
        assert not BANNED.search(html), (name, BANNED.search(html).group(0) if BANNED.search(html) else "")


def test_product_casing():
    for name, html in _pages().items():
        assert not BAD_CASE.search(html), name


def test_no_emoji_in_rendered_text():
    for name, html in _pages().items():
        text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
        text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
        assert not EMOJI.search(text), name


def test_canonical_example_and_no_leaks():
    pages = _pages()
    for name in ("/", "/solutions"):
        html = pages[name]
        for bit in EXAMPLE:
            assert bit in html, (name, bit)
    for name, html in pages.items():
        for leak in LEAKS:
            assert leak not in html, (name, leak)


def test_no_unresolved_confirm_markers():
    for name, html in _pages().items():
        assert "[CONFIRM]" not in html, name


def test_deployment_facts_come_from_shared_render():
    pages = _pages()
    for name in ("/", "/security", "/privacy", "/terms"):
        assert "Managed Cloud Workspace" in pages[name], name
        assert "AWS EC2" not in pages[name], name


def test_shared_shell():
    pages = _pages()
    for name, html in pages.items():
        assert "How it works" in html, name
        assert "Launch App" in html, name
        assert "Book a review" in html, name
        assert 'class="lang-switch"' in html, name
        assert "Archeon Cloud" in html, name


def test_infrastructure_logos():
    pages = _pages()
    for name in ("/", "/security"):
        html = pages[name]
        for logo in LOGOS:
            assert html.count(logo) == 1, (name, logo)
        assert 'alt="On-prem"' in html or "On-prem" in html
        assert 'alt="AWS"' in html
        assert 'alt="Amazon S3"' in html
    for name in ("/about", "/architecture", "/privacy", "/terms", "/solutions", "/contact-us"):
        html = pages[name]
        for logo in LOGOS:
            assert logo not in html, (name, logo)
