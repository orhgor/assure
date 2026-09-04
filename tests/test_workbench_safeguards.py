"""Workbench production safeguards: mobile lockout, tab guard, session limit."""

from __future__ import annotations

from pathlib import Path

from prompt_matrix.i18n import CATALOGS, LOCALES
from prompt_matrix.ui_cache import APP_CSS, APP_JS

ROOT = Path(__file__).resolve().parents[1]

SAFEGUARD_KEYS = (
    "safeguard.mobile.title",
    "safeguard.mobile.body",
    "safeguard.tab.title",
    "safeguard.tab.body",
    "safeguard.session.remaining",
    "safeguard.session.limit_reached",
    "safeguard.session.tooltip",
)


def test_safeguard_keys_in_every_locale() -> None:
    for locale in LOCALES:
        cat = CATALOGS[locale]
        for key in SAFEGUARD_KEYS:
            assert key in cat, f"missing {locale} {key}"
            assert str(cat[key]).strip(), f"empty {locale} {key}"


def test_workbench_markup_has_mobile_lockout() -> None:
    html = (ROOT / "prompt_matrix" / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="mobile-lockout"' in html
    assert 'data-i18n="safeguard.mobile.title"' in html
    assert 'data-i18n="safeguard.mobile.body"' in html
    assert 'id="tab-lockout-modal"' in html
    assert 'id="session-compile-limit"' in html
    assert "assure_tab_guard.js" in html
    assert "assure_session_limit.js" in html


def test_tab_guard_module_exports_init() -> None:
    js = (ROOT / "prompt_matrix" / "static" / "assure_tab_guard.js").read_text(encoding="utf-8")
    assert "assure_workbench_state" in js
    assert "AssureTabGuard" in js
    assert "tab-lockout-modal" in js


def test_session_limit_module_has_cap() -> None:
    js = (ROOT / "prompt_matrix" / "static" / "assure_session_limit.js").read_text(encoding="utf-8")
    assert "LIMIT = 15" in js
    assert "assure_session_compiles" in js
    assert "AssureSessionLimit" in js


def test_style_has_mobile_lockout_media_query() -> None:
    css = (ROOT / "prompt_matrix" / "static" / "style.css").read_text(encoding="utf-8")
    assert "#mobile-lockout" in css
    assert "max-width: 1024px" in css
    assert "#assure-app" in css


def test_ui_cache_bumped_for_safeguards() -> None:
    assert APP_CSS == "assure-62"
    assert APP_JS == "assure-51"
