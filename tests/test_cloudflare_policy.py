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


def test_legacy_domain_redirects_to_canonical():
    """getassureai.com (and www) answers with a 301 redirect to the
    https://assureproof.com canonical host - path and query are kept and the
    http->https upgrade happens in the same single hop (no chain)."""
    src = _worker()
    start = src.index('url.hostname === "getassureai.com"')
    block = src[start : src.index("}", src.index("Response.redirect", start))]
    assert "Response.redirect(consolidated.toString(), 301)" in block
    # The target is built on the https canonical host, so an http:// legacy
    # request is upgraded and canonicalized in one hop.
    assert '"https://assureproof.com" + url.pathname + url.search' in block
    # The legacy hop runs before the generic http->https rewrite, so the
    # legacy host never bounces through an intermediate assureproof http URL.
    legacy_at = src.index('url.hostname === "getassureai.com"')
    http_upgrade_at = src.index('url.protocol === "http:"')
    assert legacy_at < http_upgrade_at


def test_security_and_locale_security_redirect_to_trust():
    """/security/ and /{locale}/security/ must 301 to the matching /trust/
    route - never to /contact-us/ and never a 404 fallthrough. The worker is
    simulated at source level: the security rule is extracted from the source
    (so a renamed or reordered rule fails here) and matched against the paths
    that regressed before WP4."""
    src = _worker()
    match = re.search(r"const sec = url\.pathname\.match\(/(.+?)/i\);", src)
    assert match, "worker security redirect rule not found in source"
    js_pattern = match.group(1)
    # The extracted JS regex literal must be the security rule, not another.
    assert "security" in js_pattern and "^" in js_pattern
    security_re = re.compile(js_pattern, re.IGNORECASE)

    # Ordered rules that could otherwise capture these paths first.
    sec_at = src.index("const sec = url.pathname.match(")
    contact_at = src.index('const m = url.pathname.match(/^\\/([a-z]{2})(\\/contact)')
    app_at = src.index('url.pathname.startsWith("/app") || url.pathname.startsWith("/api")')
    assets_at = src.index("await env.ASSETS.fetch(request)")
    assert sec_at < contact_at < assets_at
    assert sec_at < app_at, "security rule must precede the /app//api -> /contact-us/ fallback"

    expectations = {
        "/security/": "/trust/",
        "/security": "/trust/",
        "/de/security/": "/de/trust/",
        "/fr/security": "/fr/trust/",
    }
    for path, target in expectations.items():
        m = security_re.match(path)
        assert m, f"/security/ rule must match {path}"
        prefix = f"/{m.group(1)}" if m.group(1) else ""
        assert prefix + "/trust/" == target, path
        # The legacy fallback must not claim these paths (that would 301 to
        # /contact-us/), and a miss would have been a 404.
        assert not path.startswith("/app") and not path.startswith("/api")

    # Paths that must NOT be captured by the security rule.
    for path in ("/securityx/", "/security/extra", "/trust/", "/"):
        assert not security_re.match(path), path

    # The portable _redirects mapping documents the same behaviour.
    redirects = (ROOT / "marketing" / "_redirects").read_text(encoding="utf-8")
    assert "/security /trust/ 301" in redirects
    assert "/security/ /trust/ 301" in redirects


def test_security_txt_expires_is_valid_future_date():
    """RFC 9116 requires a machine-readable Expires timestamp. It must parse
    as an aware UTC datetime and stay comfortably in the future so the file
    cannot silently expire (checked against the source of truth and the built
    copy)."""
    sources = [ROOT / "marketing" / "security.txt"]
    dist_copy = DIST / ".well-known" / "security.txt"
    if dist_copy.is_file():
        sources.append(dist_copy)
    now = datetime.now(timezone.utc)
    for path in sources:
        content = path.read_text(encoding="utf-8")
        expires = re.search(r"^Expires:\s*(\S+)", content, re.M)
        assert expires, f"{path}: RFC 9116 requires Expires"
        exp = datetime.fromisoformat(expires.group(1).replace("Z", "+00:00"))
        assert exp.tzinfo is not None, f"{path}: Expires must carry a timezone"
        assert exp - now >= timedelta(days=30), (
            f"{path}: Expires is within 30 days - rotate it before it silently expires"
        )
        assert exp - now <= timedelta(days=366), f"{path}: Expires must stay within a year"


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
