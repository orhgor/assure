"""Cloudflare edge policy tests (bot/redirect/endpoint hardening package).

The Worker is unit-tested at source level here (no JS runtime in CI); live
behavior is asserted in the deploy workflow's post-deploy verification step.
"""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
WORKER = ROOT / "marketing" / "cloudflare" / "marketing-worker.js"


def _worker() -> str:
    return WORKER.read_text(encoding="utf-8")


def test_worker_consolidates_getassureai_before_assureproof_redirects():
    """getassureai.com + www must 301 to https://assureproof.com with path and
    query kept, and the check must run before the www-assureproof hop so every
    legacy variant resolves in a single redirect."""
    src = _worker()
    assert 'url.hostname === "getassureai.com" || url.hostname === "www.getassureai.com"' in src
    assert '"https://assureproof.com" + url.pathname + url.search' in src
    legacy_at = src.index("getassureai.com\" || url.hostname")
    www_at = src.index("// One host: www.assureproof.com")
    assert legacy_at < www_at, "getassureai consolidation must precede the www hop"


def test_worker_rate_limits_both_post_endpoints():
    src = _worker()
    assert '"/contact-submit": { limit: 5, windowMs: 10 * 60 * 1000 }' in src
    assert '"/api/z3-demo": { limit: 30, windowMs: 60 * 1000 }' in src
    for endpoint in ("/contact-submit", "/api/z3-demo"):
        guard = f'rateLimitCheck("{endpoint}", clientIp)'
        assert guard in src, guard
    assert 'request.headers.get("CF-Connecting-IP")' in src
    assert '"Retry-After"' in src


def test_endpoints_dispatched_before_legacy_path_redirect():
    """POST endpoints must be reachable: they are handled before the /app//api
    GET fallback that 301s to /contact-us/."""
    src = _worker()
    z3_at = src.index('url.pathname === "/api/z3-demo"')
    contact_at = src.index('url.pathname === "/contact-submit"')
    fallback_at = src.index('url.pathname.startsWith("/app") || url.pathname.startsWith("/api")')
    assert z3_at < fallback_at
    assert contact_at < fallback_at


def test_worker_honeypot_unchanged():
    src = _worker()
    assert 'clean(data.company_website)' in src
    assert "honeypot: pretend success" in src


def test_worker_cors_allowlist_covers_both_domains():
    src = _worker()
    for origin in (
        "https://assureproof.com",
        "https://www.assureproof.com",
        "https://getassureai.com",
        "https://www.getassureai.com",
    ):
        assert f'"{origin}"' in src


def test_wrangler_routes_cover_both_zones_and_www():
    cfg = (ROOT / "marketing" / "cloudflare" / "wrangler-marketing-proxy.jsonc").read_text(encoding="utf-8")
    for pattern in (
        '"getassureai.com/*"',
        '"www.getassureai.com/*"',
        '"assureproof.com/*"',
        '"www.assureproof.com/*"',
    ):
        assert pattern in cfg, pattern


def test_security_txt_published_with_required_fields():
    page = DIST / ".well-known" / "security.txt"
    if not page.is_file():
        pytest.skip("dist not built")
    content = page.read_text(encoding="utf-8")
    contact = re.search(r"^Contact:\s*(\S+)", content, re.M)
    expires = re.search(r"^Expires:\s*(\S+)", content, re.M)
    canonical = re.search(r"^Canonical:\s*(\S+)", content, re.M)
    assert contact and contact.group(1).startswith("mailto:")
    assert canonical and canonical.group(1).startswith("https://assureproof.com/")
    assert expires, "RFC 9116 requires Expires"
    exp = datetime.fromisoformat(expires.group(1).replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    assert timedelta(0) < exp - now < timedelta(days=366), "Expires must be < 1 year out"


def test_robots_txt_allows_search_and_points_at_sitemap():
    page = DIST / "robots.txt"
    if not page.is_file():
        pytest.skip("dist not built")
    content = page.read_text(encoding="utf-8")
    assert "User-agent: *" in content
    assert "Allow: /" in content
    assert "Sitemap: https://assureproof.com/sitemap.xml" in content
    # No AI-crawler directives yet: Training/Agent stance is [CONFIRM].
    assert "Disallow" not in content


def test_llms_txt_matches_current_product_claims():
    page = DIST / "llms.txt"
    if not page.is_file():
        pytest.skip("dist not built")
    content = page.read_text(encoding="utf-8")
    # The pre-WP2 claim must be gone (WP2 made the Red-Hat frequency configurable).
    assert "runs on every draft and on each document's fields" not in content
    assert "configured per customer deployment" in content
    # No crawl-policy statement that could contradict robots.txt.
    assert "Disallow" not in content


def test_llms_txt_urls_resolve_to_built_pages():
    page = DIST / "llms.txt"
    if not page.is_file():
        pytest.skip("dist not built")
    content = page.read_text(encoding="utf-8")
    urls = re.findall(r"https://assureproof\.com(/[^\s]*)", content)
    assert urls, "llms.txt lists no pages"
    for url in urls:
        rel = url.strip("/")
        target = DIST / rel / "index.html" if rel else DIST / "index.html"
        assert target.is_file(), f"llms.txt links to a page that is not built: {url}"
