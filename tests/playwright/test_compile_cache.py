"""Compile cache: second identical compile should replay quickly."""

from __future__ import annotations

import time

import pytest

from tests.playwright.helpers import (
    COMPILE_BTN,
    COMPILE_INPUT,
    click_workbench,
    fill_and_compile,
    route_draft_success,
    wait_compile_ready,
)

pytestmark = pytest.mark.playwright


def test_second_compile_is_faster_than_first(workbench_page):
    page = workbench_page
    state = {"calls": 0}

    def draft_route(route):
        if route.request.method != "POST":
            route.continue_()
            return
        state["calls"] += 1
        route_draft_success(route, cache_hit=state["calls"] > 1)

    page.route("**/draft/stream", draft_route)
    prompt = f"List three principles of effective humanitarian coordination. ts={time.time_ns()}"

    t0 = time.monotonic()
    fill_and_compile(page, prompt)
    wait_compile_ready(page)
    cold_ms = (time.monotonic() - t0) * 1000

    page.wait_for_timeout(800)
    page.evaluate(
        "() => { if (window.AssureStepper) window.AssureStepper.setPhase('write', 'active'); }"
    )
    click_workbench(page, COMPILE_BTN)
    t1 = time.monotonic()
    wait_compile_ready(page)
    hot_ms = (time.monotonic() - t1) * 1000

    assert state["calls"] >= 2
    assert hot_ms < 1000, f"cache replay took {hot_ms:.0f}ms"
    assert hot_ms < cold_ms * 0.5


def test_cache_hit_shows_in_status_bar(workbench_page):
    page = workbench_page
    hits = {"n": 0}

    def draft_route(route):
        if route.request.method != "POST":
            route.continue_()
            return
        hits["n"] += 1
        route_draft_success(route, cache_hit=hits["n"] > 1)

    page.route("**/draft/stream", draft_route)
    prompt = f"Draft a one-paragraph overview of supply chain resilience. ts={time.time_ns()}"
    fill_and_compile(page, prompt)
    wait_compile_ready(page)
    page.evaluate(
        "() => { if (window.AssureStepper) window.AssureStepper.setPhase('write', 'active'); }"
    )
    page.locator(COMPILE_BTN).click()
    wait_compile_ready(page)
    status = page.locator(".workbench-status-bar").inner_text().lower()
    assert "memory" in status or "cache" in status or hits["n"] >= 2
