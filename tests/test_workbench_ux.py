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


def test_splitter_and_shortcut_sheet_markup() -> None:
    html = (ROOT / "prompt_matrix" / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="pane-splitter"' in html
    assert 'role="separator"' in html
    assert 'id="shortcut-sheet"' in html
    assert 'id="shortcut-sheet-close"' in html
    assert 'id="shortcut-hint-btn"' in html
    assert "workbench_ux.js" in html
    assert 'data-i18n="shortcuts.title"' in html
    assert 'data-i18n-aria="panes.splitter"' in html


def test_action_buttons_have_tooltips() -> None:
    html = (ROOT / "prompt_matrix" / "templates" / "index.html").read_text(encoding="utf-8")
    for key in (
        "tooltip.dock",
        "tooltip.discard",
        "tooltip.recompile",
        "tooltip.refine",
        "tooltip.model",
    ):
        assert f'data-i18n-tooltip="{key}"' in html, f"no button wired to {key}"


def test_workbench_ux_js_contract() -> None:
    js = (STATIC / "workbench_ux.js").read_text(encoding="utf-8")
    assert "AssureWorkbenchUX" in js
    assert "assure_wb_left_pct" in js
    # Shortcuts must resolve before the older window-level handlers see the key.
    assert "true" in js and "doc.addEventListener(" in js
    for token in ("pointerdown", "ArrowLeft", "localStorage"):
        assert token in js, f"missing {token}"


def test_editor_chords_are_not_stolen_from_tiptap() -> None:
    """Cmd+B is bold in TipTap. The app shortcut must yield inside a field."""
    js = (STATIC / "workbench_ux.js").read_text(encoding="utf-8")
    guard = "if (isTyping(e.target)) return;"
    assert guard in js
    # The guard has to sit above the b / backslash branches, not below them.
    assert js.index(guard) < js.index('key === "b"')
    assert js.index(guard) < js.index('key === "\\\\"')


def test_prompt_history_uses_the_shared_translator() -> None:
    """AssureI18n is not defined anywhere; __assureTf is the real global."""
    js = (STATIC / "prompt_history.js").read_text(encoding="utf-8")
    assert "__assureTf" in js
    assert "AssureI18n" not in js
    # Rows are built in JS, so a locale switch has to rebuild them.
    assert "assure:i18n" in js
