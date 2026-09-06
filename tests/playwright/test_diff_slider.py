"""Diff X-ray slider on surgical refine."""

from __future__ import annotations

import json

import pytest

from tests.playwright.helpers import (
    DIFF_XRAY,
    NODE_ID,
    REFINE_BTN,
    REFINE_FORM,
    REFINE_INSTRUCTION,
    SURGICAL_POPOVER,
    click_workbench,
    dock_document,
    fill_and_compile,
    route_draft_success,
    sample_document,
    wait_compile_ready,
)

pytestmark = pytest.mark.playwright


def test_diff_slider_appears_and_drag_updates_split(workbench_page):
    page = workbench_page
    page.route("**/draft/stream", route_draft_success)

    def refine_route(route):
        if route.request.method != "POST" or "/refine-node" not in route.request.url:
            route.continue_()
            return
        doc = sample_document(content="Revenue reached $12M in Q3.")
        node = doc["body"][0]["children"][0]
        node["content"] = "Revenue reached $12M in Q3, verified against filings."
        route.fulfill(
            status=200,
            headers={"Content-Type": "application/json"},
            body=json.dumps(
                {
                    "ok": True,
                    "node": node,
                    "document": doc,
                    "z3_results": {"status": "PASS"},
                }
            ),
        )

    page.route("**/refine-node**", refine_route)

    fill_and_compile(page, "Write two short paragraphs about disaster preparedness.")
    wait_compile_ready(page)
    dock_document(page)

    page.evaluate(
        "(nodeId) => window.AssureSurgicalClick && window.AssureSurgicalClick.open(nodeId, 480, 320)",
        NODE_ID,
    )
    page.wait_for_selector(SURGICAL_POPOVER, state="visible")
    click_workbench(page, REFINE_BTN)
    page.locator(REFINE_INSTRUCTION).fill("Make this paragraph more neutral.")
    page.locator(REFINE_FORM).evaluate("el => el.requestSubmit()")

    page.wait_for_selector(DIFF_XRAY, state="visible", timeout=60_000)
    handle = page.locator(f"{DIFF_XRAY} .diff-xray-handle")
    assert handle.is_visible()

    page.locator(f"{DIFF_XRAY} .diff-xray-handle").fill("20")
    clip_left = page.evaluate(
        """() => {
          const pane = document.querySelector('.diff-xray-original');
          return pane ? pane.style.clipPath : '';
        }"""
    )
    assert "80%" in clip_left or clip_left

    page.locator(f"{DIFF_XRAY} .diff-xray-handle").fill("80")
    clip_right = page.evaluate(
        """() => {
          const pane = document.querySelector('.diff-xray-revised');
          return pane ? pane.style.clipPath : '';
        }"""
    )
    assert "80%" in clip_right or clip_right
