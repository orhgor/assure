"""Public-copy gates that still apply after the Oct 30 style restore."""

from __future__ import annotations

import json
import re
from html import unescape
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
    r"Solo High-Stakes Creators|Journalists & Fact-Checkers|#0F5C4D",
    re.I,
)
LEAKS = ("substrate_vault", "jdf_revisions", "user_activity_log", "audit_log", "groundrails", "start_char", "end_char", "node_id")


def _pages():
    missing = [str(p) for p in PAGES.values() if not p.is_file()]
    if missing:
        pytest.skip("marketing dist is not built: " + ", ".join(missing))
    return {name: path.read_text(encoding="utf-8") for name, path in PAGES.items()}


def test_banned_strings_absent():
    for name, html in _pages().items():
        match = BANNED.search(html)
        assert match is None, (name, match.group(0) if match else "")


def test_no_table_leaks_or_confirm():
    for name, html in _pages().items():
        for leak in LEAKS:
            assert leak not in html, (name, leak)
        assert "[CONFIRM]" not in html, name


def test_home_is_the_earlier_enterprise_page():
    html = _pages()["/"]
    assert "logos-band-hero" in html
    assert "onprem-icon.svg" in html
    assert "aws-startups.svg" in html
    assert "s3-icon.svg" in html
    assert "Book a demo" in html
    assert "Four stages from the source file to a reviewed draft." in html
    assert "$1,420,000" in html and "$1,450,000" in html and "$30,000" in html
    assert "For teams that check before they act." not in html
    assert "Launch App" not in html
    assert "0F5C4D" not in html


_LDJSON_RE = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>',
    re.DOTALL,
)
_HOME_LOCALES = ("en", "es", "zh", "fr", "de", "ja", "tr", "ar")


def _home_pages() -> dict[str, str]:
    pages = {
        locale: (DIST / "index.html" if locale == "en" else DIST / locale / "index.html")
        for locale in _HOME_LOCALES
    }
    missing = [str(path) for path in pages.values() if not path.is_file()]
    if missing:
        pytest.skip("marketing dist is not built: " + ", ".join(missing))
    return {locale: path.read_text(encoding="utf-8") for locale, path in pages.items()}


def _json_ld_blocks(html: str) -> list[dict]:
    return [json.loads(unescape(raw.strip())) for raw in _LDJSON_RE.findall(html)]


def test_software_application_schema_has_category_and_operating_system():
    """Google Rich Results requires two of offers, aggregateRating, applicationCategory, operatingSystem."""
    for locale, html in _home_pages().items():
        blocks = _json_ld_blocks(html)
        apps = [block for block in blocks if block.get("@type") == "SoftwareApplication"]
        assert len(apps) == 1, (locale, [block.get("@type") for block in blocks])
        app = apps[0]
        assert app.get("applicationCategory") == "BusinessApplication", locale
        assert app.get("operatingSystem") == "Web", locale
        assert "offers" not in app, locale
        assert "aggregateRating" not in app, locale
