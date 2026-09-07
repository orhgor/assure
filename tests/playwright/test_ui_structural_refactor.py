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
    items = page.locator(".step-item")
    assert items.count() == 4
    first = items.nth(0).bounding_box()
    last = items.nth(3).bounding_box()
    assert first and last
    assert last["x"] > first["x"]
    assert abs(first["y"] - last["y"]) < 8
    row = grid.bounding_box()
    assert row
    assert last["x"] + last["width"] <= row["x"] + row["width"] + 2
    buttons = page.locator(".step-action-btn")
    assert buttons.count() >= 4
    last_btn = buttons.nth(3).bounding_box()
    assert last_btn
    assert last_btn["x"] + last_btn["width"] <= row["x"] + row["width"] + 2


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


def test_header_utilities_vertically_aligned(workbench_page):
    page = workbench_page
    boxes = page.evaluate(
        """() => {
          const header = document.querySelector('.app-header');
          const role = document.querySelector('.role-switcher');
          const actions = document.querySelector('.utility-actions');
          const download = document.querySelector('#export-menu > summary');
          if (!header || !role || !actions || !download) return null;
          const h = header.getBoundingClientRect();
          const r = role.getBoundingClientRect();
          const a = actions.getBoundingClientRect();
          const d = download.getBoundingClientRect();
          return {
            headerHeight: h.height,
            roleMid: r.top + r.height / 2,
            actionsMid: a.top + a.height / 2,
            downloadMid: d.top + d.height / 2,
            headerMid: h.top + h.height / 2,
            wrap: getComputedStyle(actions).flexWrap,
            headerPos: getComputedStyle(header).position,
          };
        }"""
    )
    assert boxes
    assert boxes["headerHeight"] >= 52
    assert abs(boxes["roleMid"] - boxes["headerMid"]) < 8
    assert abs(boxes["downloadMid"] - boxes["headerMid"]) < 10
    assert boxes["wrap"] == "nowrap"
    assert boxes["headerPos"] in ("sticky", "relative", "static")


def test_spine_sits_above_audit_rail(workbench_page):
    page = workbench_page
    page.evaluate(
        "() => window.AssureNav.switchView('generate', {replaceHash:false, persist:false})"
    )
    order = page.evaluate(
        """() => {
          const spine = document.querySelector('#argument-spine');
          const audit = document.querySelector('#role-widget-rail');
          if (!spine || !audit) return null;
          const pos = spine.compareDocumentPosition(audit);
          return (pos & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
        }"""
    )
    assert order is True


def test_jdf_canvas_card_wrap(workbench_page):
    page = workbench_page
    wrapped = page.evaluate(
        """() => {
          const target = document.querySelector('#jdf-render-target');
          return !!(target && target.closest('.jdf-card'));
        }"""
    )
    assert wrapped is True


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
