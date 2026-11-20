"""Public-copy gates that still apply after the Oct 30 style restore."""

from __future__ import annotations

import json
import re
import sys
from html import unescape
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "marketing"))
from cache_versions import LANDING_CSS  # noqa: E402

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








def _visible(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(html))).strip()






def test_v3_security_trust_faq():
    for rel in ["security/index.html", "index.html"]:
        p = DIST / rel
        if not p.is_file():
            pytest.skip("marketing dist is not built: " + rel)
        raw = p.read_text(encoding="utf-8")
        for token in ("trust-metrics-bar-inline", "trust-metrics-bar", "trust-metric-value-inline",
                      "metric-value", "0 Bytes", "Z3-Verified", "100%</div>",
                      "Page-and-line citation traceability", "External data egress"):
            assert token not in raw, (rel, token)
def test_v3_vertical_pages():
    pages = {
        "use-cases/insurance/index.html": ("Insurance claims review", "Insurance claims review"),
        "use-cases/bonus-reconciliation/index.html": ("Bonus and rebate reconciliation", "Bonus and rebate reconciliation"),
        "use-cases/government/index.html": ("Government document checks", "Government document checks"),
    }
    for rel, tokens in pages.items():
        p = DIST / rel
        if not p.is_file():
            pytest.skip("marketing dist is not built: " + rel)
        html = p.read_text(encoding="utf-8")
        assert f"landing.css?v={LANDING_CSS}" in html, rel
        t = _visible(html)
        for token in tokens:
            assert token in t, (rel, token)


def _og_png_dimensions(path: Path) -> tuple[int, int]:
    """Width/height from the PNG IHDR chunk (no Pillow dependency)."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG file"
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def test_og_image_is_1200x630_everywhere():
    """The OG image must exist and be exactly 1200x630 — source and dist copy."""
    src = ROOT / "marketing" / "static" / "og-image.png"
    assert src.is_file(), "og-image.png is missing from marketing/static"
    width, height = _og_png_dimensions(src)
    assert (width, height) == (1200, 630), (
        f"og-image.png is {width}x{height}; link previews need exactly 1200x630"
    )
    dist_img = DIST / "static" / "og-image.png"
    if dist_img.is_file():  # dist is present when the suite runs after a build
        assert dist_img.read_bytes() == src.read_bytes(), (
            "dist/static/og-image.png differs from marketing/static/og-image.png"
        )


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

STEPS = ["Upload the document package", "AssureProof reads the files; you ask for a draft",
         "It flags figures and statements that don't match the source", "A person reviews and decides"]
EN_COPY_PAGES = ["index.html", "about/index.html", "architecture/index.html", "solutions/index.html",
                 "poc/index.html", "pricing/index.html", "security/index.html", "contact-us/index.html",
                 "use-cases/index.html", "use-cases/insurance/index.html", "use-cases/government/index.html"]


def _dist_text(rel: str) -> tuple[str, str]:
    p = DIST / rel
    if not p.is_file():
        pytest.skip("marketing dist is not built: " + rel)
    html = p.read_text(encoding="utf-8")
    return html, _visible(re.sub(r"<script\b.*?</script>", " ", html, flags=re.S))


def test_home_copy_matches_the_code():
    html, t = _dist_text("index.html")
    for token in ("Upload the files. Get a draft with the mismatches marked.", "Try it on your own files", "See how it works", "runs on every draft",
                  "How AssureProof differs from extraction tools and RAG tools",
                  "Illustrative example, simulated data, not a customer result"):
        assert token in t, token
    assert '<meta property="og:image" content="https://assureproof.com/static/og-image.png">' in html


def test_four_steps_in_order_on_core_pages():
    for rel in ["index.html", "architecture/index.html", "solutions/index.html", "poc/index.html",
                "use-cases/insurance/index.html", "use-cases/government/index.html"]:
        _, t = _dist_text(rel)
        at = t.find(STEPS[0])
        assert at >= 0, (rel, STEPS[0])
        for step in STEPS[1:]:
            nxt = t.find(step, at)
            assert nxt > at, (rel, "step missing or out of order: " + step)
            at = nxt


def test_no_export_lock_or_agent_claims():
    for rel in EN_COPY_PAGES:
        _, t = _dist_text(rel)
        low = t.lower()
        for bad in ("export locked", "export blocked", "export is flagged", "export flagged",
                    "or an agent", "optional adversarial", "stops export", "nothing is approved automatically"):
            assert bad not in low, (rel, bad)


def test_poc_page_has_no_dates_or_slots():
    html, t = _dist_text("poc/index.html")
    for token in ("Yes, proceed", "Work email", "Me and a colleague (add their role)",
                  "5–10 sample cases (up to 50 files)"):
        assert token in t, token
    for bad in ("Dec 10", "Dec 20", "slot", "5-day", "zero IT setup", "free", "Me + Title", "Trust & Privacy FAQ"):
        assert bad not in t, bad
    assert "<form" in html and "/contact-submit" in html
    assert "<iframe" not in html.lower(), "no third-party form iframes"


def test_locale_poc_pages_have_full_head():
    for locale in ("es", "zh", "fr", "de", "ja", "tr", "ar"):
        html, _ = _dist_text(f"{locale}/poc/index.html")
        assert html.count('hreflang="') >= 9, locale
        assert f'<html lang="{locale}"' in html, locale
def test_home_order_and_faq_schema():
    html, t = _dist_text("index.html")
    order = ["Upload the document package", "What a check looks like",
             "How AssureProof differs from extraction tools and RAG tools",
             "Catch the mismatch before the money or the approval goes out.",
             "You choose where your documents and models run.", "Questions", "Try it on your own files."]
    at = 0
    for token in order:
        nxt = t.find(token, at)
        assert nxt >= at, token
        at = nxt
    blocks = [json.loads(unescape(b)) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
    faq = [b for b in blocks if b.get("@type") == "FAQPage"]
    assert len(faq) == 1 and len(faq[0]["mainEntity"]) == 10
    for q in faq[0]["mainEntity"]:
        assert q["name"] in t and q["acceptedAnswer"]["text"] in t, q["name"]
def test_home_icons_are_inline_svg():
    from pathlib import Path
    html = Path("dist/index.html").read_text()
    assert "data-icon=" not in html
    for cls in ("delta-icon", "use-case-icon use-case-icon--gov", "use-case-icon use-case-icon--ins", "use-case-icon use-case-icon--bonus"):
        i = html.find(f'class="{cls}"')
        assert i != -1, cls
        assert "<svg" in html[i:i + 300], cls
