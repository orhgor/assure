"""Playwright UI smoke tests for the Operator Cockpit."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.playwright.helpers import goto_founder_workbench

pytestmark = [pytest.mark.quality_check, pytest.mark.playwright]


def _mod_key(page: Page) -> str:
    return "Meta+K" if page.evaluate("() => navigator.platform.includes('Mac')") else "Control+K"


def test_cockpit_layout(page: Page, base_url: str):
    goto_founder_workbench(page, base_url)
    expect(page.locator("body.founder-workbench")).to_be_visible()
    expect(page.locator("#state-rail")).to_be_visible()
    expect(page.locator(".left-pane")).to_be_visible()
    expect(page.locator("#founder-draft-editor")).to_be_visible()


def test_cmd_k_prompt_opens(page: Page, base_url: str):
    goto_founder_workbench(page, base_url)
    page.keyboard.press(_mod_key(page))
    prompt = page.locator("#operator-prompt")
    expect(prompt).to_be_visible()
    expect(page.locator("#operator-prompt-input")).to_be_focused()


def test_cmd_k_prompt_escape_closes(page: Page, base_url: str):
    goto_founder_workbench(page, base_url)
    page.keyboard.press(_mod_key(page))
    expect(page.locator("#operator-prompt")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#operator-prompt")).to_have_class(re.compile(r"hidden"))


def test_lock_pill_opens_evidence_drawer(page: Page, base_url: str):
    goto_founder_workbench(page, base_url)
    page.evaluate(
        """() => {
          if (window.openEvidenceDrawer) {
            window.openEvidenceDrawer('abc123hash4567', { preventDefault() {}, stopPropagation() {} });
            return;
          }
          document.dispatchEvent(new CustomEvent('assure:lock-pill-click', {
            detail: { lockHash: 'abc123hash4567' },
          }));
        }"""
    )
    drawer = page.locator("#workbench-right-drawer")
    expect(drawer).to_be_visible()
    expect(page.locator("#workbench-drawer-title")).to_contain_text("Evidence")


def test_redhat_rail_button_visible(page: Page, base_url: str):
    goto_founder_workbench(page, base_url)
    expect(page.locator('#state-rail [data-stage="redhat"]')).to_be_visible()


def test_export_button_visible(page: Page, base_url: str):
    goto_founder_workbench(page, base_url)
    expect(page.locator("#btn-export-dossier")).to_be_visible()


def test_sync_status_element_present(page: Page, base_url: str):
    goto_founder_workbench(page, base_url)
    expect(page.locator("#sync-status")).to_be_attached()


def test_no_console_errors(page: Page, base_url: str):
    errors: list[str] = []

    def _capture(msg):
        if msg.type != "error":
            return
        text = msg.text or ""
        lower = text.lower()
        if "favicon" in lower:
            return
        # Blank founder draft autosave can 400 before first real edit.
        if "failed to load resource" in lower and "400" in lower:
            return
        errors.append(text)

    page.on("console", _capture)
    goto_founder_workbench(page, base_url)
    page.wait_for_timeout(2000)
    assert not errors, f"Console errors: {errors}"


def test_highlightjs_loaded(page: Page, base_url: str):
    goto_founder_workbench(page, base_url)
    hljs_present = page.evaluate("() => typeof window.hljs !== 'undefined'")
    assert hljs_present
