"""Command palette, autosave restore, and header a11y."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.playwright.helpers import COMPILE_INPUT, goto_workbench, prime_page

pytestmark = pytest.mark.playwright


def test_command_palette_cmd_k(workbench_page: Page):
    page = workbench_page
    overlay = page.locator("#command-palette-overlay")
    expect(overlay).to_be_hidden()
    page.keyboard.press("Meta+k")
    expect(overlay).to_be_visible()
    expect(page.locator("#palette-input")).to_be_focused()
    expect(page.locator(".palette-item")).to_have_count(4)
    page.locator("#palette-input").fill("analy")
    expect(page.locator(".palette-item")).to_have_count(1)
    expect(page.locator(".palette-item-name")).to_have_text("Switch to Analytics")
    page.keyboard.press("Enter")
    expect(overlay).to_be_hidden()
    expect(page.locator("#panel-analytics")).to_be_visible()


def test_command_palette_escape(workbench_page: Page):
    page = workbench_page
    page.keyboard.press("Control+k")
    expect(page.locator("#command-palette-overlay")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#command-palette-overlay")).to_be_hidden()


def test_signature_button_has_aria_label(workbench_page: Page):
    btn = workbench_page.locator("#jdf-insert-signature-btn")
    expect(btn).to_have_attribute("aria-label", "Insert signature")


def test_intent_placeholder_clears_on_type(workbench_page: Page):
    page = workbench_page
    box = page.locator(COMPILE_INPUT)
    expect(box).to_be_visible()
    page.evaluate(
        """() => {
          const el = document.getElementById('generate-intent');
          if (el) el.value = '';
        }"""
    )
    box.fill("Lock revenue at four point two million.")
    empty = page.evaluate(
        """() => {
          const el = document.getElementById('generate-intent');
          return el.value.trim().length === 0;
        }"""
    )
    assert empty is False


def test_draft_restore_toast(page: Page, base_url: str):
    prime_page(page)
    page.add_init_script(
        """
        try {
          localStorage.setItem('assure_draft_prompt', JSON.stringify({
            compile: 'Restored compile intent from storage.',
            refine: ''
          }));
        } catch (e) {}
        """
    )
    root = base_url.rstrip("/")
    page.request.put(
        f"{root}/api/projects/default/files",
        data='{"source_md":""}',
        headers={"Content-Type": "application/json"},
    )
    goto_workbench(page, base_url)
    expect(page.locator(COMPILE_INPUT)).to_have_value("Restored compile intent from storage.")
