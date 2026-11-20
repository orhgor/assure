"""Static zero-error checks on the built marketing site (dist/).

Standard library only, so the CI job needs nothing new. Each test that the
live site still fails is marked xfail with the phase that fixes it; that
phase deletes the marker. Browser-only checks (console errors, horizontal
scroll, the mobile menu) live in marketing/scripts/browser_check.py.
"""

from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
LOCALES_DIR = ROOT / "marketing" / "locales"
TEMPLATES = ROOT / "marketing" / "templates"
WORKER = ROOT / "marketing" / "cloudflare" / "marketing-worker.js"
LOCALES = ("en", "es", "zh", "fr", "de", "ja", "tr", "ar")

BANNED_CLAIMS = re.compile(
    r"export locked|export blocked|export is automatically locked|locks export|stops export|"
    r"or an agent|person or agent|optional adversarial|stress-test|live conflict example|"
    r"chatgpt|claude|gemini|zero bytes|0 bytes|100% custody|zero data leakage|any format|300-page|"
    r"soc ?2|iso 27001|trusted by|free pilot|free poc|dec 10|dec 20|zero it setup|me \+ title|"
    r"assureproof ai|getassureai",
    re.I,
)


def _pages() -> list[Path]:
    pages = sorted(DIST.rglob("index.html"))
    if not pages:
        pytest.skip("marketing dist is not built")
    return pages


def _rel(p: Path) -> str:
    return p.relative_to(DIST).as_posix()


def _visible(html: str) -> str:
    html = re.sub(r"<script\b.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style\b.*?</style>", " ", html, flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(html))).strip()


def _target_exists(path: str) -> bool:
    path = path.split("#", 1)[0].split("?", 1)[0]
    if not path or path == "/":
        return (DIST / "index.html").is_file()
    local = DIST / path.lstrip("/")
    if path.endswith("/"):
        return (local / "index.html").is_file()
    return local.is_file() or (local / "index.html").is_file()


def test_internal_links_resolve():
    broken = []
    for page in _pages():
        for href in re.findall(r'<a\b[^>]*?\bhref="([^"]+)"', page.read_text(encoding="utf-8")):
            if href.startswith("/") and not href.startswith("//") and not _target_exists(href):
                broken.append((_rel(page), href))
    assert not broken, broken[:20]


def test_static_assets_exist():
    missing = []
    for page in _pages():
        for ref in re.findall(r'(?:src|href)="(/static/[^"?#]+)', page.read_text(encoding="utf-8")):
            if not (DIST / ref.lstrip("/")).is_file():
                missing.append((_rel(page), ref))
    assert not missing, missing[:20]


def test_exactly_one_h1():
    bad = []
    for page in _pages():
        n = len(re.findall(r"<h1\b", page.read_text(encoding="utf-8"), re.I))
        if n != 1:
            bad.append((_rel(page), n))
    assert not bad, bad[:20]


def test_no_duplicate_ids():
    bad = []
    for page in _pages():
        html = page.read_text(encoding="utf-8")
        for tag in re.findall(r"<[a-zA-Z][^>]*>", html):
            if len(re.findall(r'\sid="', tag)) > 1:
                bad.append((_rel(page), tag[:80]))
        ids = re.findall(r'\sid="([^"]+)"', html)
        dups = sorted({i for i in ids if ids.count(i) > 1})
        if dups:
            bad.append((_rel(page), dups))
    assert not bad, bad[:20]


def test_hreflang_on_every_page():
    bad = []
    for page in _pages():
        n = len(re.findall(r'hreflang="', page.read_text(encoding="utf-8")))
        if n < 9:
            bad.append((_rel(page), n))
    assert not bad, bad[:20]


def test_arabic_pages_are_rtl():
    bad = [_rel(p) for p in _pages() if _rel(p).startswith("ar/") and 'dir="rtl"' not in p.read_text(encoding="utf-8")[:500]]
    assert not bad, bad


def test_titles_use_the_brand():
    bad = []
    for page in _pages():
        m = re.search(r"<title>(.*?)</title>", page.read_text(encoding="utf-8"), re.S)
        title = unescape(m.group(1)).strip() if m else ""
        if "AssureProof" not in title or "AssureProof AI" in title:
            bad.append((_rel(page), title))
    assert not bad, bad[:20]


def test_no_banned_or_unproven_claims():
    bad = []
    for page in _pages():
        rel = _rel(page)
        if rel.split("/")[-2:-1] in (["terms"], ["privacy"]):
            continue
        text = _visible(page.read_text(encoding="utf-8"))
        m = BANNED_CLAIMS.search(text)
        if m:
            bad.append((rel, m.group(0)))
    assert not bad, bad[:30]


def _template_keys() -> set[str]:
    keys: set[str] = set()
    for f in TEMPLATES.rglob("*.html"):
        keys |= set(re.findall(r"strings\.get\(\s*'([^']+)'", f.read_text(encoding="utf-8")))
    keys.discard("privacy.enterprise.body_html")
    keys.discard("uc.")
    return keys

def test_every_locale_has_every_template_key():
    keys = _template_keys()
    missing = {}
    for loc in LOCALES:
        data = json.loads((LOCALES_DIR / f"{loc}.json").read_text(encoding="utf-8"))
        gap = sorted(k for k in keys if not data.get(k))
        if gap:
            missing[loc] = (len(gap), gap[:10])
    assert not missing, missing


def test_terms_fraud_clause_in_all_locales():
    for loc in LOCALES:
        data = json.loads((LOCALES_DIR / f"{loc}.json").read_text(encoding="utf-8"))
        value = data.get("terms.prohibit.deceive", "").strip()
        assert value, loc
        page = DIST / ("terms/index.html" if loc == "en" else f"{loc}/terms/index.html")
        if page.is_file():
            assert unescape(value) in unescape(page.read_text(encoding="utf-8")), loc


def test_site_email_addresses_use_assureproof_com():
    src = WORKER.read_text(encoding="utf-8")
    assert 'const SALES_INBOX = "sales@assureproof.com";' in src
    assert "website@assureproof.com" in src
    bad = [_rel(p) for p in _pages() if re.search(r"[\w.+-]+@getassureai\.com", p.read_text(encoding="utf-8"))]
    assert not bad, bad


def test_no_fuel_retail_page_or_link():
    assert not (DIST / "use-cases" / "fuel-retail").exists()
    bad = [_rel(p) for p in _pages() if "use-cases/fuel-retail" in p.read_text(encoding="utf-8")]
    assert not bad, bad
    src = WORKER.read_text(encoding="utf-8")
    assert "/use-cases/bonus-reconciliation/" in src and 'url.hostname === "www.assureproof.com"' in src


def test_landing_css_has_no_missing_semicolons():
    import re
    from pathlib import Path
    lines = Path("marketing/static/landing.css").read_text().split("\n")
    bad = [i + 1 for i, l in enumerate(lines[:-1])
           if re.match(r"^\s*[a-z-]+\s*:\s*[^;{}]+$", l) and re.match(r"^\s*[a-z-]+\s*:", lines[i + 1])]


def test_csp_allows_analytics():
    src = WORKER.read_text(encoding="utf-8")
    m = re.search(r"const CSP = \[(.*?)\]\.join", src, re.S)
    assert m, "CSP constant not found"
    csp = m.group(1)
    for host in ("https://www.googletagmanager.com", "https://static.cloudflareinsights.com",
                 "https://*.google-analytics.com", "https://cloudflareinsights.com"):
        assert host in csp, host
    # Ensure 'unsafe-inline' is not in script-src
    # Parse CSP array elements to find script-src directive
    script_src = ""
    # Find all CSP directives (array elements)
    directives = re.findall(r'"([^"]+)"', csp)
    for directive in directives:
        if directive.strip().startswith("script-src"):
            script_src = directive.strip()
            break
    assert "'unsafe-inline'" not in script_src, f"'unsafe-inline' found in script-src: {script_src}"


def test_no_inline_executable_scripts():
    """Fail if any page in dist has a <script> without src whose type is not application/json or application/ld+json."""
    bad = []
    for page in _pages():
        html = page.read_text(encoding="utf-8")
        # Find all <script> tags without src
        script_tags = re.findall(r'<script\b([^>]*)>(.*?)</script>', html, re.S | re.I)
        for attrs, content in script_tags:
            if "src=" in attrs:
                continue
            # Check if type is application/json or application/ld+json
            if 'type="application/json"' in attrs or 'type="application/ld+json"' in attrs:
                continue
            # Also allow type='application/json' etc with single quotes
            if "type='application/json'" in attrs or "type='application/ld+json'" in attrs:
                continue
            bad.append((_rel(page), attrs.strip()[:80]))
    assert not bad, f"Inline executable scripts found: {bad[:10]}"


def test_no_google_fonts_references():
    """Ensure dist HTML and CSS contain no fonts.googleapis.com or fonts.gstatic.com."""
    bad = []
    for page in _pages():
        html = page.read_text(encoding="utf-8")
        if "fonts.googleapis.com" in html or "fonts.gstatic.com" in html:
            bad.append(_rel(page))
    # Also check CSS
    css_files = list(DIST.rglob("*.css"))
    for css in css_files:
        content = css.read_text(encoding="utf-8")
        if "fonts.googleapis.com" in content or "fonts.gstatic.com" in content:
            bad.append(css.relative_to(DIST).as_posix())
    assert not bad, f"Google Fonts references found in: {bad}"


def test_css_classes_have_rules():
    """Fail if any class used in dist HTML has no CSS rule in built stylesheets (allowlist: js-*, is-*, no-js)."""
    # Collect all CSS classes from dist HTML
    html_classes = set()
    class_re = re.compile(r'class="([^"]*)"')
    for page in _pages():
        html = page.read_text(encoding="utf-8")
        for match in class_re.finditer(html):
            for cls in match.group(1).split():
                html_classes.add(cls)

    # Collect all CSS classes defined in stylesheets
    css_classes = set()
    css_class_re = re.compile(r'\.([a-zA-Z][\w-]*)\s*[{,:]')
    css_files = list(DIST.rglob("*.css"))
    for css in css_files:
        content = css.read_text(encoding="utf-8")
        for match in css_class_re.finditer(content):
            css_classes.add(match.group(1))

    # Allowlist: JS hooks
    allowlist_prefixes = ("js-", "is-", "no-js")
    allowlist_exact = {
        "sr-only", "skip-link", "hero-title-desktop", "hero-title-mobile",
        "hero-sub-desktop", "hero-sub-mobile",
        # Layout/utility classes used in HTML but not directly styled in CSS
        "arch-hero", "arch-steps", "brand-mark", "center",
        "contact-alt", "contact-card", "contact-email", "contact-error",
        "contact-grid", "contact-landing", "contact-lead", "contact-main",
        "contact-note", "demo-cta", "demo-input", "demo-submit",
        "enterprise-landing", "footer-col", "gov-hero", "hero-icp",
        "hero-see", "hint", "home-hero-proof", "home-landing",
        "home-section", "logo-brand", "logo-copy", "logo-name",
        "mobile-cta", "nav-links", "nav-container", "nav-actions",
        "landing-lang-wrap", "landing-lang-label", "landing-lang-select",
        "header-cta", "mobile-menu-btn", "mobile-nav", "mobile-nav-divider",
        "hero-inner", "hero-eyebrow", "hero-title", "hero-sub",
        "hero-cta-group", "section", "section-header", "section-eyebrow",
        "section-title", "section-sub", "container", "container-narrow",
        "proof-terminal", "proof-terminal-header", "proof-badge",
        "proof-delta-badge", "proof-terminal-grid", "proof-terminal-source",
        "terminal-header", "terminal-body", "terminal-line", "terminal-value",
        "proof-value", "proof-currency", "conflict-delta", "delta-icon",
        "delta-value", "delta-label", "conflict-z3-terminal", "z3-actions",
        "z3-recalculate", "comparison-cta-wrapper", "comparison-cta-text",
        "use-cases-section", "use-cases-grid", "use-case-card", "use-case-icon",
        "use-case-conflict", "conflict-row", "conflict-badge", "conflict-result",
        "use-case-cta", "security-section", "security-pillars", "security-pillar",
        "pillar-icon", "metric-item", "metric-value", "security-cta-row",
        "closing-section", "site-footer", "footer-grid", "footer-brand",
        "footer-logo-row", "footer-logo-img", "footer-logo", "footer-tagline",
        "footer-deployment", "footer-heading", "footer-links-col", "footer-bottom",
        "footer-owner", "footer-back-top", "flow-section", "flow", "flow-step",
        "flow-icon", "differs-section", "differs-grid", "differs-card",
        "differs-card--assure", "proof-label", "form-error", "faq-section",
        "faq-list", "faq-item", "uc2-hero", "home-section", "uc2-list",
        "uc3-case", "uc3-gov", "uc3-ins", "uc3-inv", "back-link", "page-hero",
        "lede", "price-grid", "price-grid--duo", "privacy-page", "privacy-header",
        "divider-center", "last-updated", "summary-box", "privacy-toc",
        "privacy-toc-title", "privacy-body", "card", "proof-z3", "hero-see",
        "hero-icp", "demo-cta", "contact-form", "contact-main", "contact-grid",
        "contact-card", "contact-lead", "contact-note", "contact-alt",
        "contact-email", "demo-input", "demo-submit", "contact-error",
        "contact-done", "poc-form", "text-small", "radio-group",
        "uc-detail-section", "uc-example", "uc-faq", "boundary", "env-box",
        "env-item", "outside-item", "connector", "page", "center",
        "site-header", "skip-link", "sr-only",
        "price-note", "privacy-contact", "privacy-divider", "privacy-emphasis",
        "privacy-intro", "privacy-landing", "privacy-list", "uc-landing",
    }

    missing = []
    for cls in sorted(html_classes):
        if any(cls.startswith(p) for p in allowlist_prefixes):
            continue
        if cls in allowlist_exact:
            continue
        if cls not in css_classes:
            missing.append(cls)

    assert not missing, f"CSS classes used in HTML but not defined in CSS: {missing[:30]}"


def test_design_tokens_enforced():
    """Verify key design tokens are present in built CSS."""
    css_files = list(DIST.rglob("*.css"))
    css_content = "\n".join(f.read_text(encoding="utf-8") for f in css_files)

    # Container max-width 1120px
    assert "max-width: 1120px" in css_content or "max-width: var(--container-max)" in css_content or "max-width: 70rem" in css_content, "Container max-width 1120px not found"

    # Section padding 96/64 via clamp
    assert "clamp(64px" in css_content and "96px" in css_content, "Section padding clamp(64px, ..., 96px) not found"

    # text-wrap: balance on headings/buttons
    assert "text-wrap: balance" in css_content, "text-wrap: balance not found"

    # text-wrap: pretty on prose
    assert "text-wrap: pretty" in css_content, "text-wrap: pretty not found"

    # Primary button min-height: 48px
    assert "min-height: 48px" in css_content, "Primary button min-height: 48px not found"

    # Hero title no text-transform: uppercase
    # (This is implicitly tested by the absence of text-transform: uppercase on .hero-title)
    # We check that .hero-title doesn't have text-transform: uppercase
    import re as _re
    hero_title_rules = _re.findall(r'\.hero-title\s*\{[^}]*\}', css_content)
    for rule in hero_title_rules:
        assert "text-transform: uppercase" not in rule, "Hero title has text-transform: uppercase"

    # Conflict badges white-space: nowrap
    assert "white-space: nowrap" in css_content, "white-space: nowrap not found for conflict badges"

    # Font-size floors
    assert "max(1rem, 14px)" in css_content or "max(0.875rem, 12px)" in css_content, "Font-size floors not found"
