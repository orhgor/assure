"""Structural workbench refactor + compiler error handling."""

from __future__ import annotations

from playwright.sync_api import expect

from tests.playwright.helpers import COMPILE_BTN, COMPILE_INPUT


def test_context_switching(workbench_page):
    page = workbench_page
    page.evaluate(
        "() => window.AssureNav.switchView('projects', {replaceHash:false, persist:false})"
    )
    expect(page.locator("#panel-write")).to_be_visible()
    page.evaluate(
        "() => window.AssureNav.switchView('generate', {replaceHash:false, persist:false})"
    )
    expect(page.locator("#panel-draft")).to_be_visible()
    page.evaluate("() => window.AssureNav.switchView('vault', {replaceHash:false, persist:false})")
    expect(page.locator("#panel-sources")).to_be_visible()
    page.evaluate(
        "() => window.AssureNav.switchView('analytics', {replaceHash:false, persist:false})"
    )
    expect(page.locator("#panel-analytics")).to_be_visible()


def test_middle_pane_isolation(workbench_page):
    page = workbench_page
    page.evaluate(
        "() => window.AssureNav.switchView('generate', {replaceHash:false, persist:false})"
    )
    expect(page.locator("#panel-draft")).to_be_visible()
    expect(page.locator("#panel-write")).to_be_hidden()
    expect(page.locator("#panel-sources")).to_be_hidden()
    expect(page.locator("#panel-analytics")).to_be_hidden()


def test_right_canvas_onboarding(workbench_page):
    page = workbench_page
    page.evaluate(
        "() => window.AssureNav.switchView('projects', {replaceHash:false, persist:false})"
    )
    expect(page.locator("#canvas-onboarding-state")).to_be_visible()
    expect(page.locator("#canvas-onboarding-state")).to_contain_text("workspace")


def test_legacy_footer_absent(workbench_page):
    page = workbench_page
    expect(page.locator("footer.command-deck")).to_have_count(0)


def test_stepper_grid_bounds(workbench_page):
    page = workbench_page
    page.evaluate(
        "() => window.AssureNav.switchView('generate', {replaceHash:false, persist:false})"
    )
    grid = page.locator(".stepper-timeline")
    expect(grid).to_be_visible()
    buttons = page.locator(".step-action-btn")
    assert buttons.count() >= 4
    first = buttons.nth(0).bounding_box()
    last = buttons.nth(3).bounding_box()
    assert first and last
    assert last["x"] > first["x"]
    assert abs(first["y"] - last["y"]) < 40
    row = grid.bounding_box()
    assert row
    assert last["x"] + last["width"] <= row["x"] + row["width"] + 2


def test_status_bar_scope(workbench_page):
    page = workbench_page
    page.evaluate(
        "() => window.AssureNav.switchView('projects', {replaceHash:false, persist:false})"
    )
    expect(page.locator("#workbench-status-bar")).to_be_hidden()
    page.evaluate(
        "() => window.AssureNav.switchView('generate', {replaceHash:false, persist:false})"
    )
    expect(page.locator("#workbench-status-bar")).to_be_visible()


def test_accordion_icons_aligned(workbench_page):
    page = workbench_page
    page.evaluate(
        "() => window.AssureNav.switchView('generate', {replaceHash:false, persist:false})"
    )
    display = page.evaluate(
        """() => {
          const el = document.querySelector('#argument-spine > summary');
          if (!el) return '';
          const s = getComputedStyle(el);
          return s.display + '|' + s.alignItems;
        }"""
    )
    assert "flex" in display
    assert "center" in display


def test_analytics_layout(workbench_page):
    page = workbench_page
    page.evaluate(
        "() => window.AssureNav.switchView('analytics', {replaceHash:false, persist:false})"
    )
    kpis = page.locator("#analytics-kpis")
    expect(kpis).to_be_visible()
    assert page.locator("#analytics-kpis .analytics-kpi").count() == 4
    grid = page.locator("#analytics-charts")
    expect(grid).to_be_visible()
    main = page.locator("#analytics-charts .analytics-chart-main").bounding_box()
    side = page.locator("#analytics-charts .analytics-table-card").bounding_box()
    assert main and side
    assert main["width"] > side["width"]
    assert abs(main["y"] - side["y"]) < 40


def test_compiler_error_handling(workbench_page):
    page = workbench_page
    page.evaluate(
        "() => window.AssureNav.switchView('generate', {replaceHash:false, persist:false})"
    )
    page.route(
        "**/api/projects/**/draft/stream",
        lambda route: route.fulfill(
            status=400,
            content_type="application/json",
            body='{"error":"payload rejected"}',
        ),
    )
    page.locator(COMPILE_INPUT).fill("Q3 investor update with revenue 4.2M")
    page.locator(COMPILE_BTN).click()
    page.wait_for_timeout(1500)
    busy = page.locator(COMPILE_BTN).get_attribute("aria-busy")
    assert busy in (None, "false")
    expect(page.locator("#generate-compile-btn")).not_to_have_class("is-busy")
    expect(page.locator(".toast")).to_contain_text("payload rejected")
