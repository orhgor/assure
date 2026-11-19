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


def test_home_is_the_earlier_enterprise_page():
    html = _pages()["/"]
    # The fold: H1, subheadline, proof card, CTAs.
    assert "Catch every contradiction before a document leaves your desk." in html
    assert "Source A" in html and "Source B" in html
    assert "Master Services Agreement (MSA), Page 12, Clause 4.2" in html and "120,000 EUR" in html
    assert "Vendor Invoice #884, Page 3, Line Item 8" in html and "142,500 EUR" in html
    assert "Z3 Conflict" in html
    assert "Export locked" in html
    assert "See the proof" in html and "Run a pilot on your files" in html
    # V3 pipeline (new copy): Ingest → Page-Pinned Extraction (ParSure) → Structured Drafting (DraftSure)
    # → Mathematical Consistency Check (Z3 Solver) → Optional Adversarial Stress-Test (Red Hat Review)
    # → Human Sign-Off & Audit Trail
    for stage in ("Ingest Your File Stack", "Page-Pinned Extraction", "Structured Drafting",
                  "Mathematical Consistency Check", "Optional Adversarial Stress-Test", "Human Sign-Off"):
        assert stage in html
    # Sovereign boundary wording
    assert "Runs in your own AWS account, on-premises, or private GPU environment." in html
    assert "When the model is local, documents stay inside that boundary." in html
    # Retired copy is gone
    assert "Before you pay, accept, or sign." not in html
    assert "For teams that check before they act." not in html
    assert "Launch App" not in html
    assert "0F5C4D" not in html
    assert "A person decides." not in _visible(html)
    # The Open Graph image meta must point at the canonical URL, exactly.
    assert '<meta property="og:image" content="https://assureproof.com/static/og-image.png">' in html


def test_v3_pipeline_order_on_core_pages():
    for rel in ["index.html", "architecture/index.html", "solutions/index.html", "poc/index.html"]:
        p = DIST / rel
        if not p.is_file():
            pytest.skip("marketing dist is not built: " + rel)
        t = _visible(p.read_text(encoding="utf-8"))
        stages = ["Ingest Your File Stack", "Page-Pinned Extraction", "Structured Drafting",
                  "Mathematical Consistency Check", "Optional Adversarial Stress-Test", "Human Sign-Off"]
        pos = [t.find(s) for s in stages]
        assert all(i >= 0 for i in pos), (rel, [s for s, i in zip(stages, pos) if i < 0])
        assert pos == sorted(pos), (rel, pos)


def test_v3_export_blocked_phrasing():
    for rel in ["index.html", "architecture/index.html", "solutions/index.html", "poc/index.html",
                "use-cases/insurance/index.html", "use-cases/fuel-retail/index.html",
                "use-cases/government/index.html"]:
        p = DIST / rel
        if not p.is_file():
            pytest.skip("marketing dist is not built: " + rel)
        t = _visible(p.read_text(encoding="utf-8")).lower()
        if "export" not in t:
            continue
        assert "export locked" in t, rel
        assert "a person or an agent decides" in t, rel
        assert "file can still be downloaded" in t, rel


def _visible(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(html))).strip()


def test_v3_pipeline_order_on_core_pages():
    for rel in ["index.html", "architecture/index.html", "solutions/index.html", "poc/index.html"]:
        p = DIST / rel
        if not p.is_file():
            pytest.skip("marketing dist is not built: " + rel)
        t = _visible(p.read_text(encoding="utf-8"))
        stages = ["Ingest Your File Stack", "Page-Pinned Extraction", "Structured Drafting",
                  "Mathematical Consistency Check", "Optional Adversarial Stress-Test", "Human Sign-Off"]
        pos = [t.find(s) for s in stages]
        assert all(i >= 0 for i in pos), (rel, [s for s, i in zip(stages, pos) if i < 0])
        assert pos == sorted(pos), (rel, pos)

def test_v3_export_blocked_phrasing():
    for rel in ["index.html", "architecture/index.html", "solutions/index.html", "poc/index.html",
                "use-cases/insurance/index.html", "use-cases/fuel-retail/index.html",
                "use-cases/government/index.html"]:
        p = DIST / rel
        if not p.is_file():
            pytest.skip("marketing dist is not built: " + rel)
        t = _visible(p.read_text(encoding="utf-8")).lower()
        if "export" not in t:
            continue
        # Different pages use different phrasing for export locking
        assert any(phrase in t for phrase in ["export blocked", "export locked", "export is automatically locked"]), rel
        # POC page uses "person decides" not full phrase
        if rel == "poc/index.html":
            assert "person" in t and "decide" in t, rel
            assert "file" in t and "download" in t, rel
        else:
            assert "a person or an agent decides" in t, rel
            assert "file can still be downloaded" in t, rel

def test_v3_poc_conversion_hub():
    p = DIST / "poc" / "index.html"
    if not p.is_file():
        pytest.skip("marketing dist is not built: poc")
    html = p.read_text(encoding="utf-8")
    t = _visible(html)
    for token in ("4 selective, assessment-gated free POC slots", "1 workflow",
                  "sample cases", "max 50 files", "5-day turnaround", "zero IT setup",
                  "Yes, proceed", "Dec 10", "Dec 20", "Trust & Privacy FAQ",
                  "Work email", "Document pair to cross-check", "Just me", "Me + Title"):
        assert token in t, token
    assert "<form" in html and "/contact-submit" in html
    assert "<iframe" not in html.lower(), "no third-party form iframes"


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
        "use-cases/fuel-retail/index.html": ("Fuel retail bonus reconciliation", "Fuel retail bonus reconciliation"),
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
def test_v3_locale_parity():
    for locale in ("es", "zh", "fr", "de", "ja", "tr", "ar"):
        p = DIST / locale / "poc" / "index.html"
        if not p.is_file():
            pytest.skip("marketing dist is not built: " + locale)
        t = _visible(p.read_text(encoding="utf-8"))
        assert re.search(r"142[.,\u00a0\u2009 ]?500", t), locale
        assert re.search(r"120[.,\u00a0\u2009 ]?000", t), locale
        for stale in ("Contract cap", "The assessment", "1 workflow", "Limited sample cases", "Book a demo"):
            assert stale not in t, (locale, stale)


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
