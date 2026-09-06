"""Full compile and selection compile flows."""

from __future__ import annotations

import pytest

from tests.playwright.helpers import (
    COMPILE_BTN,
    COMPILE_INPUT,
    DOCK_BTN,
    fill_and_compile,
    route_draft_success,
    wait_compile_ready,
)

pytestmark = pytest.mark.playwright


def test_full_compile_enables_dock(workbench_page):
    page = workbench_page
    page.route("**/draft/stream", route_draft_success)
    prompt = "Summarize disaster preparedness for field teams in two sentences."
    fill_and_compile(page, prompt)
    wait_compile_ready(page)
    assert page.locator(DOCK_BTN).is_enabled()
    assert page.locator("#generate-node-count").inner_text().strip() != "0"


def test_selection_compile_sends_selection_payload(workbench_page):
    page = workbench_page
    captured: list[dict] = []

    def capture(route):
        if route.request.method == "POST":
            captured.append(route.request.post_data_json or {})
            route_draft_success(route)
            return
        route.continue_()

    page.route("**/draft/stream", capture)
    full = "Alpha paragraph about supply chains. Beta paragraph about cold chain monitoring."
    page.locator(COMPILE_INPUT).fill(full)
    page.locator(COMPILE_INPUT).evaluate(
        """(el) => {
          el.focus();
          el.setSelectionRange(0, el.value.indexOf('.') + 1);
        }"""
    )
    page.locator(COMPILE_BTN).click()
    wait_compile_ready(page)
    assert captured, "expected draft/stream POST"
    payload = captured[-1]
    assert payload.get("compileType") == "selection" or payload.get("compile_type") == "selection"
    assert payload.get("content")
    assert page.locator(DOCK_BTN).is_enabled()
