"""Workbench status bar updates on compile and cache."""

from __future__ import annotations

import pytest

from tests.playwright.helpers import (
    COMPILE_BTN,
    COMPILE_INPUT,
    COMPILE_STATUS_BAR,
    draft_stream_success,
    fill_and_compile,
    fulfill_sse,
    route_draft_success,
    wait_compile_ready,
)

pytestmark = pytest.mark.playwright

HEALTH = "#workbench-health"


def test_status_bar_verified_after_compile(workbench_page):
    page = workbench_page
    page.route("**/draft/stream", route_draft_success)
    fill_and_compile(page, "Brief note on quarterly revenue.")
    wait_compile_ready(page)
    text = page.locator(HEALTH).inner_text()
    assert "Verified" in text or "✅" in text


def test_status_bar_cache_flash(workbench_page):
    page = workbench_page

    def cached_route(route):
        if route.request.method == "POST":
            fulfill_sse(route, draft_stream_success(cache_hit=True))
            return
        route.continue_()

    page.route("**/draft/stream", cached_route)
    page.locator(COMPILE_INPUT).fill("Cached compile prompt for status bar.")
    page.locator(COMPILE_BTN).click()
    wait_compile_ready(page)
    page.wait_for_function(
        f"""() => {{
          const bar = document.querySelector('{COMPILE_STATUS_BAR}');
          return bar && (bar.classList.contains('is-cache-hit') || /Cached|⚡/.test(document.querySelector('{HEALTH}')?.textContent || ''));
        }}""",
        timeout=15_000,
    )
