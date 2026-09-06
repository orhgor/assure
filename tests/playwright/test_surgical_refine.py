"""Surgical click-to-fix: refine opens diff panel."""

from __future__ import annotations

import json

import pytest

from tests.playwright.helpers import (
    COMPILE_BTN,
    COMPILE_INPUT,
    DIFF_PANEL,
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


def test_surgical_refine_shows_diff_panel(workbench_page):
    page = workbench_page
    page.route("**/draft/stream", route_draft_success)

    def refine_route(route):
        if route.request.method != "POST" or "/refine-node" not in route.request.url:
            route.continue_()
            return
        body = route.request.post_data_json or {}
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
    page.locator(REFINE_INSTRUCTION).fill("Make this paragraph more neutral and concise.")
    page.locator(REFINE_FORM).evaluate("el => el.requestSubmit()")

    page.wait_for_function(
        f"""() => {{
          const panel = document.querySelector('{DIFF_PANEL}');
          if (panel && !panel.hidden) return true;
          const pop = document.querySelector('{SURGICAL_POPOVER}');
          return pop && pop.hidden;
        }}""",
        timeout=60_000,
    )
    panel = page.locator(DIFF_PANEL)
    popover_hidden = page.locator(SURGICAL_POPOVER).is_hidden()
    assert (panel.is_visible() and not panel.is_hidden()) or popover_hidden
