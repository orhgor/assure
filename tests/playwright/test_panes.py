"""Founder workbench pane layout — Playwright E2E tests (Phase 2)."""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import Page, expect

from tests.playwright.helpers import goto_founder_workbench
from tests.playwright.test_founder_workbench import SAMPLE_RUN, _dismiss_overlays, _mock_runs_api

pytestmark = pytest.mark.playwright

RUNS_LIST_RE = re.compile(r"/api/runs(\?.*)?$")


def test_state_rail_visible(page: Page, base_url: str):
    goto_founder_workbench(page, base_url)
    rail = page.locator("#state-rail")
    expect(rail).to_be_visible()
    width = page.evaluate(
        """() => {
          const el = document.getElementById('state-rail');
          return el ? Math.round(el.getBoundingClientRect().width) : 0;
        }"""
    )
    assert width == 48
    expect(rail.locator('[data-rail="draft"] .state-rail-icon')).to_contain_text("⚡")
    expect(rail.locator('[data-rail="sources"] .state-rail-icon')).to_contain_text("📂")
    expect(rail.locator('[data-rail="runs"] .state-rail-icon')).to_contain_text("⚙️")
    expect(rail.locator('[data-rail="audit"] .state-rail-icon')).to_contain_text("🛡️")


def test_toggle_left(page: Page, base_url: str):
    _mock_runs_api(page)
    goto_founder_workbench(page, base_url)
    _dismiss_overlays(page)
    container = page.locator(".app-container.founder-workbench")
    expect(container).to_have_class(re.compile(r"pane-left-open"))
    page.locator("body").click(position={"x": 400, "y": 400})
    page.keyboard.press("Meta+B")
    expect(container).to_have_class(re.compile(r"pane-left-closed"), timeout=5_000)
    page.keyboard.press("Meta+B")
    expect(container).to_have_class(re.compile(r"pane-left-open"), timeout=5_000)


def test_toggle_right(page: Page, base_url: str):
    page.route(
        RUNS_LIST_RE,
        lambda route: route.fulfill(
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "runs": [
                        {
                            **SAMPLE_RUN,
                            "redhat_findings": [
                                {
                                    "id": "rh_pw_pane",
                                    "run_id": SAMPLE_RUN["id"],
                                    "title": "Gap",
                                    "content": "Missing citation.",
                                    "severity": "medium",
                                    "status": "open",
                                }
                            ],
                        }
                    ],
                    "count": 1,
                }
            ),
        ),
    )
    goto_founder_workbench(page, base_url)
    _dismiss_overlays(page)
    container = page.locator(".app-container.founder-workbench")
    drawer = page.locator("#workbench-right-drawer")
    expect(drawer).to_be_hidden()
    page.locator("body").click(position={"x": 400, "y": 400})
    page.keyboard.press("Meta+I")
    expect(container).to_have_class(re.compile(r"pane-right-open"), timeout=5_000)
    expect(drawer).to_be_visible()
    page.keyboard.press("Meta+I")
    expect(container).not_to_have_class(re.compile(r"pane-right-open"), timeout=5_000)


def test_inline_diff(page: Page, base_url: str):
    page.route(
        RUNS_LIST_RE,
        lambda route: route.fulfill(
            content_type="application/json",
            body=json.dumps({"ok": True, "runs": [dict(SAMPLE_RUN)], "count": 1}),
        ),
    )
    goto_founder_workbench(page, base_url)
    page.wait_for_function(
        "() => document.querySelector('#founder-draft-editor .ProseMirror')",
        timeout=15_000,
    )
    editor = page.locator("#founder-draft-editor .ProseMirror")
    editor.click()
    editor.type("Existing draft paragraph.")
    expect(page.locator(".run-card")).to_have_count(1, timeout=15_000)
    page.locator(".run-btn-draft").first.click()
    diff = page.locator("#founder-inline-diff")
    expect(diff).to_be_visible(timeout=5_000)
    expect(diff.locator(".founder-diff-remove")).to_contain_text("Existing draft paragraph")
    expect(diff.locator(".founder-diff-add")).to_contain_text("Revenue reached $12M in Q3.")
    diff.locator(".founder-diff-block").click()
    page.keyboard.press("Meta+Enter")
    expect(diff).to_be_hidden(timeout=5_000)
    expect(editor).to_contain_text("Revenue reached $12M in Q3.", timeout=5_000)

    editor.click()
    editor.press("Control+A")
    editor.type("Another baseline draft.")
    page.locator(".run-btn-draft").first.click()
    expect(diff).to_be_visible(timeout=5_000)
    diff.locator(".founder-diff-block").click()
    page.keyboard.press("Backspace")
    expect(diff).to_be_hidden(timeout=5_000)
    expect(editor).to_contain_text("Another baseline draft.", timeout=5_000)


def test_command_palette_polish(page: Page, base_url: str):
    goto_founder_workbench(page, base_url)
    _dismiss_overlays(page)
    page.locator("body").click(position={"x": 10, "y": 10})
    page.keyboard.press("Meta+K")
    modal = page.locator(".command-bar-modal")
    expect(modal).to_be_visible(timeout=5_000)
    styles = page.evaluate(
        """() => {
          const modal = document.querySelector('.command-bar-modal');
          const overlay = document.getElementById('command-bar-overlay');
          if (!modal || !overlay) return null;
          const modalStyle = getComputedStyle(modal);
          const overlayStyle = getComputedStyle(overlay);
          return {
            boxShadow: modalStyle.boxShadow,
            backdropFilter: overlayStyle.backdropFilter || overlayStyle.webkitBackdropFilter,
            inputFontSize: getComputedStyle(document.getElementById('command-bar-input')).fontSize,
          };
        }"""
    )
    assert styles is not None
    assert "25px 50px" in styles["boxShadow"]
    assert styles["backdropFilter"] and "blur" in styles["backdropFilter"]
    assert styles["inputFontSize"] == "18px"
