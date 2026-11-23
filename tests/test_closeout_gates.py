"""Final close-out gates: WP4 /trust/, WP7 dedup, generated llms.txt.

Standard library only, follows the dist-based pattern of test_site_static.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
LOCALES_DIR = ROOT / "marketing" / "locales"
WORKER = ROOT / "marketing" / "cloudflare" / "marketing-worker.js"
RENDER = ROOT / "marketing" / "render.py"
LOCALES = ("en", "es", "zh", "fr", "de", "ja", "tr", "ar")

FACT_KEYS = (
    "trust.facts.title",
    "trust.disclosure.text",
    "trust.subprocessors.title",
    "trust.dpa.link",
    "security.pillar1.desc",
    "security.diagram.model",
    "arch.pipe.5.text",
    "arch.lim.files",
)


def test_trust_route_registered_and_worker_301():
    render = RENDER.read_text(encoding="utf-8")
    assert '"template": "trust.html"' in render
    assert '"output": "trust/index.html"' in render
    assert '"template": "security.html"' not in render
    src = WORKER.read_text(encoding="utf-8")
    assert "security\\/?$" in src, "worker must 301 /security/ to /trust/"
    assert "/trust/" in src


def test_trust_page_renders_shared_facts_in_every_locale():
    """WP4: /trust/ must render the shared deployment facts, subprocessors and
    disclosure copy in every locale - values come from the shared catalog."""
    missing = {}
    for loc in LOCALES:
        base = DIST if loc == "en" else DIST / loc
        page = base / "trust" / "index.html"
        if not page.is_file():
            missing[loc] = "page missing"
            continue
        html = page.read_text(encoding="utf-8")
        catalog = json.loads((LOCALES_DIR / f"{loc}.json").read_text(encoding="utf-8"))
        problems = [key for key in FACT_KEYS if not catalog.get(key) or catalog[key] not in html]
        if problems:
            missing[loc] = problems
    assert not missing, f"/trust/ shared-facts parity failed: {missing}"


def test_privacy_toc_title_appears_once():
    """Regression: the 'On this page' label must not render twice."""
    page = DIST / "privacy" / "index.html"
    if not page.is_file():
        pytest.skip("dist not built")
    html = page.read_text(encoding="utf-8")
    visible = re.sub(r"<[^>]+>", " ", re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S))
    assert visible.count("On this page") == 1


def test_about_links_out_instead_of_duplicating():
    """WP7: /about/ links to /architecture/ and /trust/ instead of duplicating."""
    page = DIST / "about" / "index.html"
    if not page.is_file():
        pytest.skip("dist not built")
    html = page.read_text(encoding="utf-8")
    assert 'href="/architecture/"' in html
    assert 'href="/trust/"' in html
    visible = re.sub(r"<[^>]+>", " ", re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S))
    assert "How a check works" not in visible
    assert "Runs where you choose" not in visible


def test_poc_links_to_architecture_without_flow_dup():
    page = DIST / "poc" / "index.html"
    if not page.is_file():
        pytest.skip("dist not built")
    html = page.read_text(encoding="utf-8")
    assert 'href="/architecture/"' in html
    en = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    assert en["poc.scope"] in html


def test_terms_date_heading_appears_once():
    page = DIST / "terms" / "index.html"
    if not page.is_file():
        pytest.skip("dist not built")
    html = page.read_text(encoding="utf-8")
    assert len(re.findall(r'data-i18n="terms.updated"', html)) == 1


def test_llms_txt_is_generated_from_shared_facts():
    page = DIST / "llms.txt"
    if not page.is_file():
        pytest.skip("dist not built")
    content = page.read_text(encoding="utf-8")
    assert "## Deployment facts" in content
    assert "## Key pages" in content
    assert "Trust center: https://assureproof.com/trust/" in content
    en = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    assert en["security.pillar1.desc"] in content, "llms facts drifted from en.json"
    assert en["arch.lim.files"] in content
    assert "/security/" not in content, "llms.txt still links the moved route"


def test_security_route_not_built():
    if not (DIST / "index.html").is_file():
        pytest.skip("dist not built")
    assert not (DIST / "security").exists(), "stale /security/ pages in dist"


FLOW_BLOCK_MARKERS = ('id="how-it-works"', 'data-i18n="flow.title"')


def test_no_four_step_flow_block_on_poc_and_architecture():
    """/poc/ and /architecture/ must link out instead of duplicating the
    landing four-step 'How it works' block - in every locale."""
    bad = {}
    for loc in LOCALES:
        base = DIST if loc == "en" else DIST / loc
        for page in ("poc/index.html", "architecture/index.html"):
            path = base / page
            if not path.is_file():
                bad[(loc, page)] = "missing"
                continue
            html = path.read_text(encoding="utf-8")
            hits = [m for m in FLOW_BLOCK_MARKERS if m in html]
            if hits:
                bad[(loc, page)] = hits
    assert not bad, f"four-step flow block still rendered: {bad}"


def test_discovery_routes_resolve():
    """/evidence/ and /trust/ resolve in every locale; /llms.txt and
    /.well-known/security.txt resolve at the root."""
    missing = []
    for loc in LOCALES:
        base = DIST if loc == "en" else DIST / loc
        for rel in ("evidence/index.html", "trust/index.html"):
            if not (base / rel).is_file():
                missing.append(f"{loc}/{rel}")
    for rel in ("llms.txt", ".well-known/security.txt"):
        if not (DIST / rel).is_file():
            missing.append(rel)
    assert not missing, missing


def test_footer_links_evidence_in_every_locale():
    """The footer must surface /evidence/ with the localized page title
    ('Evidence and method'), in every locale."""
    bad = {}
    for loc in LOCALES:
        base = DIST if loc == "en" else DIST / loc
        page = base / "index.html"
        if not page.is_file():
            bad[loc] = "home missing"
            continue
        html = page.read_text(encoding="utf-8")
        catalog = json.loads((LOCALES_DIR / f"{loc}.json").read_text(encoding="utf-8"))
        label = catalog["evidence.title"]
        href = 'href="/evidence/"' if loc == "en" else f'href="/{loc}/evidence/"'
        visible = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S)
        visible = re.sub(r"<[^>]+>", " ", visible)
        problems = []
        if href not in html:
            problems.append("link")
        if label not in visible:
            problems.append("label")
        if problems:
            bad[loc] = problems
    assert not bad, f"footer evidence link failed: {bad}"

