"""Playwright coverage for the workbench UX polish pass (density, splitter,
focus states, keyboard capture, i18n).

Aligned with the real DOM in ``templates/index.html`` and the actual behavior
in ``static/style.css`` / ``static/workbench_ux.js`` — not an idealized one.
Follows the same live-server + pytest-playwright pattern as
``test_user_simulation.py``: no ``/compile`` route, no JS test runner, no
invented class names.

Run locally:
  uv sync --extra dev
  uv run pytest tests/e2e/test_workbench_ux_e2e.py -v --tb=short
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.playwright

# Selectors copied from templates/index.html — not guessed.
COMPILE_INPUT = "#generate-intent"
COMPILE_BTN = "#generate-compile-btn"
DOCK_BTN = "#generate-accept-dock-phase"
WORKBENCH = "#jdf-workbench"
LEFT_PANE = "#left-pane"
SPLITTER = "#pane-splitter"
SHORTCUT_SHEET = "#shortcut-sheet"
SHORTCUT_HINT_BTN = "#shortcut-hint-btn"
LOCALE_SELECT = "#locale-select"
PROMPT_HISTORY_HOST = "#generate-prompt-history"
ONBOARDING_KEY = "assure_onboarding_complete"
PANE_STORAGE_KEY = "assure_wb_left_pct"


def _app_url(base_url: str) -> str:
    env_base = os.environ.get("ASSURE_BASE_URL")
    root = (env_base or base_url).rstrip("/")
    return f"{root}/app"


def _goto_workbench(page, base_url: str):
    """Matches _goto_workbench in test_user_simulation.py: skip onboarding,
    wait for the JDF manager, land on the Compile view."""
    page.add_init_script(f"try {{ localStorage.setItem({ONBOARDING_KEY!r}, '1'); }} catch (e) {{}}")
    page.goto(_app_url(base_url), wait_until="domcontentloaded")
    page.wait_for_selector(WORKBENCH, state="visible")
    page.wait_for_function(
        "() => window.__assureJdf && typeof window.__assureJdf.render === 'function'"
    )
    page.wait_for_function("() => !document.body.classList.contains('onboarding-active')")
    page.wait_for_selector(COMPILE_BTN, state="visible")
    return page


class TestDensity:
    """The polish pass tightened control chrome; the document view was not
    supposed to shrink."""

    def test_intent_textarea_padding_and_type(self, page, base_url):
        _goto_workbench(page, base_url)
        metrics = page.locator(COMPILE_INPUT).evaluate(
            """el => {
                const cs = getComputedStyle(el);
                return {
                    paddingTop: parseFloat(cs.paddingTop),
                    paddingLeft: parseFloat(cs.paddingLeft),
                    fontSize: parseFloat(cs.fontSize),
                    lineHeight: parseFloat(cs.lineHeight),
                };
            }"""
        )
        # --wb-control-y / --wb-control-x tightened the control chrome; this
        # locks the intent by range so a token rename does not need editing.
        assert 6 <= metrics["paddingTop"] <= 10
        assert 9 <= metrics["paddingLeft"] <= 13
        assert metrics["fontSize"] == pytest.approx(16, abs=0.5)
        assert metrics["lineHeight"] == pytest.approx(25.6, abs=0.5)

    def test_left_pane_padding_uses_density_token(self, page, base_url):
        _goto_workbench(page, base_url)
        padding = page.locator(LEFT_PANE).evaluate(
            "el => parseFloat(getComputedStyle(el).paddingLeft)"
        )
        assert 12 <= padding <= 16

    def test_disabled_dock_button_is_not_a_wait_cursor(self, page, base_url):
        """A disabled control is not a loading control — this was the actual
        bug (cursor: wait), not a missing hover state."""
        _goto_workbench(page, base_url)
        dock = page.locator(DOCK_BTN)
        assert dock.is_disabled()
        cursor = dock.evaluate("el => getComputedStyle(el).cursor")
        assert cursor == "not-allowed"


class TestPaneSplitter:
    def test_splitter_present_and_default_width(self, page, base_url):
        _goto_workbench(page, base_url)
        splitter = page.locator(SPLITTER)
        assert splitter.is_visible()
        wb_left = page.locator(WORKBENCH).evaluate(
            "el => getComputedStyle(el).getPropertyValue('--wb-left').trim()"
        )
        # 42% is the deliberate default — the canvas gets more room than the
        # compile pane, not a 50/50 split.
        assert wb_left == "42%"

    def test_drag_clamps_and_persists_across_reload(self, page, base_url):
        _goto_workbench(page, base_url)
        page.evaluate(f"try {{ localStorage.removeItem({PANE_STORAGE_KEY!r}); }} catch (e) {{}}")

        box = page.locator(SPLITTER).bounding_box()
        assert box is not None
        start_x = box["x"] + box["width"] / 2
        start_y = box["y"] + box["height"] / 2

        page.mouse.move(start_x, start_y)
        page.mouse.down()
        # Drag far past the right clamp (68%) — the splitter must stop there,
        # not follow the cursor off the edge of the pane.
        page.mouse.move(start_x + 900, start_y, steps=8)
        page.mouse.up()

        clamped = page.locator(WORKBENCH).evaluate(
            "el => getComputedStyle(el).getPropertyValue('--wb-left').trim()"
        )
        assert clamped == "68%"
        stored = page.evaluate(f"localStorage.getItem({PANE_STORAGE_KEY!r})")
        assert stored == "68"

        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(WORKBENCH, state="visible")
        after_reload = page.locator(WORKBENCH).evaluate(
            "el => getComputedStyle(el).getPropertyValue('--wb-left').trim()"
        )
        assert after_reload == "68%"

    def test_double_click_resets_to_the_default_not_fifty_fifty(self, page, base_url):
        _goto_workbench(page, base_url)
        page.evaluate(f"try {{ localStorage.setItem({PANE_STORAGE_KEY!r}, '60'); }} catch (e) {{}}")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(WORKBENCH, state="visible")

        page.locator(SPLITTER).dblclick()
        reset = page.locator(WORKBENCH).evaluate(
            "el => getComputedStyle(el).getPropertyValue('--wb-left').trim()"
        )
        assert reset == "42%"


class TestFocusStates:
    def test_focus_visible_ring_uses_trust_blue_not_a_default_browser_color(self, page, base_url):
        _goto_workbench(page, base_url)
        page.locator(COMPILE_BTN).focus()
        outline_color = page.locator(COMPILE_BTN).evaluate(
            "el => getComputedStyle(el).outlineColor"
        )
        # var(--color-primary) is Trust Blue (#1A4B8C) = rgb(26, 75, 140).
        # This is not emerald green and was never intended to be.
        assert outline_color == "rgb(26, 75, 140)"

    def test_mouse_focus_does_not_show_a_ring(self, page, base_url):
        """:focus-visible suppresses the ring for pointer interaction."""
        _goto_workbench(page, base_url)
        page.locator(COMPILE_INPUT).click()
        page.locator(COMPILE_BTN).click(force=True)
        outline_style = page.locator(COMPILE_BTN).evaluate(
            "el => getComputedStyle(el).outlineStyle"
        )
        assert outline_style == "none"


class TestKeyboardCapture:
    def test_shortcut_sheet_opens_with_question_mark_outside_a_field(self, page, base_url):
        _goto_workbench(page, base_url)
        page.locator("body").click(position={"x": 5, "y": 5})
        page.keyboard.press("?")
        page.wait_for_selector(SHORTCUT_SHEET, state="visible")
        page.keyboard.press("Escape")
        page.wait_for_selector(SHORTCUT_SHEET, state="hidden")

    def test_question_mark_stays_inert_while_typing(self, page, base_url):
        _goto_workbench(page, base_url)
        page.locator(COMPILE_INPUT).click()
        page.keyboard.type("what about ?")
        sheet_hidden = page.locator(SHORTCUT_SHEET).evaluate("el => el.hidden")
        assert sheet_hidden is True
        assert page.locator(COMPILE_INPUT).input_value() == "what about ?"

    def test_cmd_b_bolds_in_tiptap_instead_of_toggling_the_sidebar(self, page, base_url):
        """Cmd+B is bold in TipTap. The app-level shortcut must yield to the
        editor instead of stealing the chord — this was a real bug caught
        during manual verification, not a hypothetical."""
        _goto_workbench(page, base_url)
        editor = page.locator(".ProseMirror")
        if editor.count() == 0:
            pytest.skip("TipTap editor not mounted in this build")
        editor.click()
        editor.type("hello")
        # ProseMirror binds Mod-b, and the conftest launches real macOS Chrome
        # (channel="chrome"), so Mod resolves to Meta here, not Control.
        mod = "Meta" if page.evaluate("() => navigator.platform.includes('Mac')") else "Control"
        page.keyboard.press(f"{mod}+A")
        page.keyboard.press(f"{mod}+B")
        is_bold = editor.evaluate("el => !!el.querySelector('strong, b')")
        assert is_bold, f"{mod}+B was intercepted by the app shortcut instead of TipTap"


class TestI18n:
    def test_locale_switch_translates_the_shortcut_sheet(self, page, base_url):
        _goto_workbench(page, base_url)
        page.select_option(LOCALE_SELECT, "tr")
        page.wait_for_function("() => document.documentElement.lang === 'tr'")
        page.keyboard.press("?")
        page.wait_for_selector(SHORTCUT_SHEET, state="visible")
        sheet_text = page.locator(SHORTCUT_SHEET).inner_text()
        assert "Klavye kısayolları" in sheet_text
        assert "Compile the draft" not in sheet_text

    def test_locale_switch_rebuilds_recent_prompt_history(self, page, base_url):
        """prompt_history.js used to call a nonexistent AssureI18n global and
        never rebuild on a language switch; this is the regression test for
        that specific fix."""
        _goto_workbench(page, base_url)
        page.evaluate(
            "() => window.AssurePromptHistory && "
            "window.AssurePromptHistory.push('Q3 update', {status: 'ok', nodes: 3})"
        )
        page.select_option(LOCALE_SELECT, "tr")
        page.wait_for_function("() => document.documentElement.lang === 'tr'")
        reuse_label = page.locator(f"{PROMPT_HISTORY_HOST} .btn-outline").first.inner_text()
        assert reuse_label.strip() == "Yeniden kullan"
