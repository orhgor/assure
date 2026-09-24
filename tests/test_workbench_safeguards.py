"""Workbench production safeguards: mobile lockout, tab guard, session limit."""

from __future__ import annotations

from pathlib import Path

from prompt_matrix.i18n import CATALOGS, LOCALES
from prompt_matrix.ui_cache import APP_CSS, APP_JS

ROOT = Path(__file__).resolve().parents[1]

SAFEGUARD_KEYS = (
    "workbench.status.health_cached",
    "safeguard.mobile.title",
    "safeguard.mobile.body",
    "safeguard.tab.title",
    "safeguard.tab.body",
    "safeguard.tab.continue",
    "safeguard.session.remaining",
    "safeguard.session.limit_reached",
    "safeguard.session.tooltip",
    "unsaved.switch_project",
    "unsaved.switch_view",
    "unsaved.switch_generating",
    "audit.title",
    "audit.deck",
    "audit.description",
    "audit.tooltip",
    "audit.export_now",
    "audit.toast_exported",
    "audit.toast_share",
    "jdf.menu.hint",
    "jdf.menu.edit",
    "jdf.menu.revise",
    "jdf.menu.reprompt",
    "jdf.menu.revision",
    "jdf.menu.revise_intent",
    "jdf.menu.reprompt_intent",
    "jdf.menu.revision_intent",
)


def test_catalogs_drop_corporate_lockout_copy() -> None:
    for locale in LOCALES:
        blob = " ".join(str(v) for v in CATALOGS[locale].values())
        assert "Kurumsal" not in blob, locale
        assert "Desktop required" not in blob, locale
    for locale in LOCALES:
        cat = CATALOGS[locale]
        for key in SAFEGUARD_KEYS:
            assert key in cat, f"missing {locale} {key}"
            assert str(cat[key]).strip(), f"empty {locale} {key}"


def test_style_keeps_workbench_usable_on_mobile() -> None:
    css = (ROOT / "prompt_matrix" / "static" / "style.css").read_text(encoding="utf-8")
    assert "#mobile-lockout" in css
    assert ".mobile-hint" in css
    assert "max-width: 1024px" in css
    assert "#assure-app {\n    display: none !important;" not in css


def test_ui_cache_bumped_for_safeguards() -> None:
    assert APP_CSS == "assure-99"
    assert APP_JS == "assure-99"
