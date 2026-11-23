import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
WORKER = ROOT / "marketing" / "cloudflare" / "marketing-worker.js"
HEADERS_FILE = ROOT / "marketing" / "_headers"

LOCALES = ["de", "es", "fr", "tr", "ja", "zh", "ar"]


def _worker_source() -> str:
    if not WORKER.is_file():
        pytest.skip("worker source not found")
    return WORKER.read_text(encoding="utf-8")


def test_worker_sets_the_full_security_header_contract():
    """WP8: the Worker must set HSTS, nosniff, referrer policy and a CSP that
    includes frame-ancestors 'none' — for every response it serves."""
    src = _worker_source()
    for directive in [
        '"X-Content-Type-Options": "nosniff"',
        '"X-Frame-Options": "DENY"',
        '"Referrer-Policy": "strict-origin-when-cross-origin"',
        '"Permissions-Policy": "camera=(), microphone=(), geolocation=()"',
        '"Strict-Transport-Security": "max-age=31536000; includeSubDomains"',
    ]:
        assert directive in src, directive
    csp_block = re.search(r"const CSP = \[(.*?)\]\.join", src, re.S)
    assert csp_block, "CSP block not found in worker"
    csp = csp_block.group(1)
    for part in [
        '"default-src \'self\'"',
        '"script-src \'self\' https://www.googletagmanager.com https://static.cloudflareinsights.com"',
        '"style-src \'self\' \'unsafe-inline\'"',
        '"font-src \'self\'"',
        '"connect-src \'self\'',
        '"frame-ancestors \'none\'"',
        '"base-uri \'self\'"',
        '"form-action \'self\'"',
    ]:
        assert part in csp, part


def test_headers_file_mirrors_the_worker():
    """The _headers file is the Pages-portable copy of the Worker policy and
    must not contradict it (no stale preload, same CSP)."""
    src = _worker_source()
    assert HEADERS_FILE.is_file(), "marketing/_headers missing"
    content = HEADERS_FILE.read_text(encoding="utf-8")
    assert "preload" not in content, "_headers must match the worker HSTS (no preload)"
    for value in [
        "max-age=31536000; includeSubDomains",
        "nosniff",
        "strict-origin-when-cross-origin",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "camera=(), microphone=(), geolocation=()",
    ]:
        assert value in content, value
    csp_block = re.search(r"const CSP = \[(.*?)\]\.join", src, re.S).group(1)
    worker_csp = "; ".join(re.findall(r'"([^"]+)"', csp_block))
    assert worker_csp in content, "_headers CSP drifted from the worker CSP"


def _dist_page(locale: str, rel: str) -> Path:
    base = DIST if locale == "en" else DIST / locale
    return base / rel


@pytest.mark.parametrize("locale", ["en"] + LOCALES)
def test_lang_attribute_matches_locale(locale: str):
    page = _dist_page(locale, "index.html")
    if not page.is_file():
        pytest.skip("dist not built")
    html = page.read_text(encoding="utf-8")
    m = re.search(r'<html lang="([^"]+)"([^>]*)>', html)
    assert m, "html lang attribute missing"
    assert m.group(1) == locale, (locale, m.group(1))
    if locale == "ar":
        assert 'dir="rtl"' in m.group(2), "ar must render RTL"


@pytest.mark.parametrize("locale", ["en"] + LOCALES)
def test_skip_link_present_on_every_homepage(locale: str):
    page = _dist_page(locale, "index.html")
    if not page.is_file():
        pytest.skip("dist not built")
    html = page.read_text(encoding="utf-8")
    assert 'href="#main-content"' in html, f"skip link missing ({locale})"


def test_images_have_explicit_dimensions_and_alt():
    page = _dist_page("en", "index.html")


class _NameAudit(HTMLParser):
    """Collect interactive elements and verify each has an accessible name."""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.problems = []
        self.label_for = set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "label" and a.get("for", "").strip():
            self.label_for.add(a["for"].strip())
        if tag == "img":
            if not a.get("alt", "").strip():
                self.problems.append(f'img without alt: {a.get("src", "?")}')
            return
        if tag in ("a", "button", "select", "input", "textarea"):
            if tag == "input" and a.get("type") == "hidden":
                return
            if a.get("aria-hidden") == "true":
                return
            self.stack.append({"tag": tag, "text": "", "attrs": a})

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1]["tag"] == tag:
            el = self.stack.pop()
            a = el["attrs"]
            named = (
                el["text"].strip()
                or a.get("aria-label", "").strip()
                or a.get("aria-labelledby")
                or a.get("title", "").strip()
                or (a.get("id") in self.label_for)
            )
            if not named:
                self.problems.append(
                    f'{el["tag"]} without accessible name: {a.get("href", a.get("id", "?"))}'
                )

    def handle_data(self, data):
        if self.stack:
            self.stack[-1]["text"] += data


@pytest.mark.parametrize(
    "rel", ["index.html", "poc/index.html", "contact-us/index.html"]
)
def test_interactive_elements_have_accessible_names(rel: str):
    """Keyboard-pass proxy: every focusable control on Home, /poc/ and
    /contact-us/ must expose an accessible name (text, aria-label, label-for)."""
    page = _dist_page("en", rel)
    if not page.is_file():
        pytest.skip("dist not built")
    html = re.sub(r"<script\b.*?</script>", " ", page.read_text(encoding="utf-8"), flags=re.S)
    auditor = _NameAudit()
    auditor.feed(html)
    assert not auditor.problems, (rel, auditor.problems)

    if not page.is_file():
        pytest.skip("dist not built")
    for tag in re.findall(r"<img [^>]*>", page.read_text(encoding="utf-8")):
        assert "alt=" in tag, tag
        assert re.search(r'\bwidth="\d+"', tag), tag
        assert re.search(r'\bheight="\d+"', tag), tag

