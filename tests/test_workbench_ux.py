"""Workbench UX polish: density tokens, interaction states, panes, shortcuts."""

from __future__ import annotations

from pathlib import Path

from prompt_matrix.i18n import CATALOGS, LOCALES

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "prompt_matrix" / "static"

UX_KEYS = (
    "panes.splitter",
    "panes.reset",
    "shortcuts.title",
    "shortcuts.compile",
    "shortcuts.refine",
    "shortcuts.dock",
    "shortcuts.export",
    "shortcuts.save",
    "shortcuts.undo",
    "shortcuts.redo",
    "shortcuts.sidebar",
    "shortcuts.panes",
    "shortcuts.help",
    "shortcuts.panes_hint",
    "shortcuts.dock_unavailable",
    "tooltip.dock",
    "tooltip.discard",
    "tooltip.recompile",
    "tooltip.refine",
    "tooltip.stress",
    "tooltip.model",
    "export.started",
)


def test_ux_i18n_keys_present_in_every_locale() -> None:
    for locale in LOCALES:
        cat = CATALOGS[locale]
        for key in UX_KEYS:
            assert key in cat, f"missing {locale} {key}"
            assert str(cat[key]).strip(), f"empty {locale} {key}"


def test_ux_strings_are_actually_translated() -> None:
    """A catalog that falls back to **EN leaves the workbench English."""
    english = CATALOGS["en"]
    for locale in LOCALES:
        if locale == "en":
            continue
        cat = CATALOGS[locale]
        for key in UX_KEYS:
            assert cat[key] != english[key], f"{locale} {key} still English"


def test_export_tooltip_covers_all_three_formats() -> None:
    """The menu offers Word, Markdown, and HTML — the tooltip must not say Word only."""
    for locale in LOCALES:
        value = CATALOGS[locale]["tooltip.export"]
        assert "Markdown" in value, f"{locale} tooltip.export omits Markdown"
        assert "HTML" in value, f"{locale} tooltip.export omits HTML"


def test_motion_and_density_tokens_defined() -> None:
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    for token in (
        "--motion-fast:",
        "--motion-base:",
        "--ease-out:",
        "--wb-control-y:",
        "--wb-pane-pad:",
        "--wb-splitter:",
        "--wb-left:",
        "--wb-focus-ring:",
    ):
        assert token in css, f"missing token {token}"


def test_interaction_states_present() -> None:
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert ":focus-visible" in css
    assert "prefers-reduced-motion" in css
    assert ".pane-splitter" in css
    assert "dock-ready-glow" in css
    assert "is-pane-resizing" in css
    # A disabled control must not claim to be loading.
    assert "cursor: not-allowed;" in css
