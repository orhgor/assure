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
    return keys


@pytest.mark.xfail(reason="until phase 12", strict=False)
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


def test_csp_allows_analytics():
    src = WORKER.read_text(encoding="utf-8")
    m = re.search(r"const CSP = \[(.*?)\]\.join", src, re.S)
    assert m, "CSP constant not found"
    csp = m.group(1)
    for host in ("https://www.googletagmanager.com", "https://static.cloudflareinsights.com",
                 "https://*.google-analytics.com", "https://cloudflareinsights.com"):
        assert host in csp, host


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
