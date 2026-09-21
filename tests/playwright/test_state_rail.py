"""Founder workbench state rail — Playwright E2E tests."""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import Page, expect

from tests.playwright.helpers import goto_founder_workbench
from tests.playwright.test_founder_workbench import SAMPLE_RUN, _dismiss_overlays, _mock_runs_api

pytestmark = pytest.mark.playwright

RUNS_LIST_RE = re.compile(r"/api/runs(\?.*)?$")


def _mock_runs_with_variants(page: Page) -> None:
    runs = [
        dict(SAMPLE_RUN),
        {
            **SAMPLE_RUN,
            "id": "run_pw_grounded02",
            "directive": "Grounded narrative",
            "title": "Grounded narrative",
            "extracted_locks": SAMPLE_RUN["extracted_locks"],
            "status": "stamped",
            "redhat_findings": [],
        },
        {
            **SAMPLE_RUN,
            "id": "run_pw_redhat03",
            "directive": "Audit candidate",
            "title": "Audit candidate",
            "extracted_locks": [],
            "status": "draft",
            "redhat_findings": [
                {
                    "id": "rh_pw_2",
                    "run_id": "run_pw_redhat03",
                    "title": "Gap",
                    "content": "Missing citation.",
                    "severity": "medium",
                    "status": "open",
                    "highlight_text": "Revenue",
                }
            ],
        },
        {
            **SAMPLE_RUN,
            "id": "run_pw_ungrounded04",
            "directive": "Ungrounded draft",
            "title": "Ungrounded draft",
            "extracted_locks": [],
            "status": "draft",
            "redhat_findings": [],
        },
    ]

    def handle_runs(route):
        if route.request.method == "GET":
            route.fulfill(
                content_type="application/json",
                body=json.dumps({"ok": True, "runs": runs, "count": len(runs)}),
            )
            return
        route.continue_()

    page.route(RUNS_LIST_RE, handle_runs)


def test_state_rail_width(page: Page, base_url: str):
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


def test_state_rail_filters_run_cards(page: Page, base_url: str):
    _mock_runs_with_variants(page)
    goto_founder_workbench(page, base_url)
    expect(page.locator(".run-card")).to_have_count(4, timeout=15_000)

    page.locator("body").click(position={"x": 400, "y": 400})
    page.keyboard.press("Shift+3")
    page.wait_for_function(
        "() => document.getElementById('runs-stack')?.getAttribute('data-filter') === 'grounded'",
        timeout=5_000,
    )
    expect(page.locator(".run-card")).to_have_count(2)
    expect(page.locator(".runs-stack-empty")).to_have_count(0)

    page.locator('#state-rail [data-stage="redhat"]').click()
    page.wait_for_function(
        "() => document.getElementById('runs-stack')?.getAttribute('data-filter') === 'redhat'",
        timeout=5_000,
    )
    expect(page.locator(".run-card")).to_have_count(1)
    expect(page.locator(".run-card").first).to_contain_text("Audit candidate")

    page.locator('#state-rail [data-stage="runs"]').click()
    expect(page.locator(".run-card")).to_have_count(4)


def test_state_rail_empty_filter_message(page: Page, base_url: str):
    page.route(
        RUNS_LIST_RE,
        lambda route: route.fulfill(
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "runs": [dict(SAMPLE_RUN, extracted_locks=[], redhat_findings=[])],
                    "count": 1,
                }
            ),
        ),
    )
    goto_founder_workbench(page, base_url)
    expect(page.locator(".run-card")).to_have_count(1, timeout=15_000)
    page.locator("body").click(position={"x": 400, "y": 400})
    page.keyboard.press("Shift+3")
    expect(page.locator(".runs-stack-empty")).to_be_visible(timeout=5_000)
    expect(page.locator(".runs-stack-empty")).to_contain_text("Attach sources")


def test_shortcuts_suppressed_in_tiptap(page: Page, base_url: str):
    _mock_runs_api(page)
    goto_founder_workbench(page, base_url)
    page.wait_for_function(
        "() => document.querySelector('#founder-draft-editor .ProseMirror')",
        timeout=15_000,
    )
    editor = page.locator("#founder-draft-editor .ProseMirror")
    editor.click()
    editor.type("typing should not trigger rail filters")
    page.keyboard.press("Shift+2")
    expect(page.locator("#command-bar-overlay")).to_be_hidden()
    page.keyboard.press("Meta+K")
    expect(page.locator("#command-bar-overlay")).to_be_visible(timeout=5_000)


def test_shortcuts_work_outside_editor(page: Page, base_url: str):
    _mock_runs_api(page)
    goto_founder_workbench(page, base_url)
    _dismiss_overlays(page)
    page.locator("body").click(position={"x": 10, "y": 10})
    page.keyboard.press("Shift+1")
    expect(page.locator("#command-bar-overlay")).to_be_visible(timeout=5_000)
    _dismiss_overlays(page)
    page.locator('#state-rail [data-stage="runs"]').click()
    page.keyboard.press("Shift+3")
    expect(page.locator("#runs-stack")).to_have_attribute("data-filter", "grounded")


def test_show_workspaces_absent(page: Page, base_url: str):
    goto_founder_workbench(page, base_url)
    expect(page.locator("#founder-show-workspaces")).to_have_count(0)


def test_header_baseline_alignment(page: Page, base_url: str):
    goto_founder_workbench(page, base_url)
    tops = page.evaluate(
        """() => {
          const runsHeader = document.querySelector('.founder-sidebar-head.runs-stack-header');
          const draftHeader = document.querySelector('.founder-draft-header');
          if (!runsHeader || !draftHeader) return null;
          return {
            runsTop: runsHeader.getBoundingClientRect().top,
            draftTop: draftHeader.getBoundingClientRect().top,
            runsPad: getComputedStyle(runsHeader).paddingTop,
            draftPad: getComputedStyle(draftHeader).paddingTop,
          };
        }"""
    )
    assert tops is not None
    assert abs(tops["runsTop"] - tops["draftTop"]) < 2
    assert tops["runsPad"] == "16px"
    assert tops["draftPad"] == "16px"
