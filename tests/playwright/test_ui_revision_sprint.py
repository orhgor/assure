"""UI revision sprint — stepper, embedded analytics, provenance drawer."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests.playwright.helpers import (
    PROVENANCE_PANEL,
    goto_workbench,
    prime_page,
)

pytestmark = pytest.mark.playwright


def test_stepper_workflow_renders(page, base_url):
    prime_page(page)
    goto_workbench(page, base_url)
    stepper = page.locator(".stepper-timeline")
    expect(stepper).to_be_visible()
    expect(page.locator('.step-item[data-phase="write"]')).to_be_visible()
    expect(page.locator('.step-item[data-phase="verify"]')).to_be_visible()
    expect(page.locator('.step-item[data-phase="audit"]')).to_be_visible()
    expect(page.locator('.step-item[data-phase="ship"]')).to_be_visible()
    expect(page.locator("#generate-compile-btn")).to_be_visible()
    expect(page.locator("#generate-accept-dock-phase")).to_be_visible()
    assert page.locator("#generate-accept-dock").count() == 0
    assert page.locator("#draft-preview-dock-btn").count() == 0


def test_analytics_embedded_constraints(page, base_url):
    prime_page(page)
    goto_workbench(page, base_url)
    page.evaluate(
        "() => window.AssureNav && window.AssureNav.switchView('analytics', {replaceHash: false, persist: false})"
    )
    page.wait_for_selector("#view-analytics", state="visible")
    expect(page.locator("#assure-app")).to_be_visible()
    chart_card = page.locator(".analytics-chart-card").first
    expect(chart_card).to_be_visible()
    box = chart_card.bounding_box()
    assert box is not None
    assert box["height"] <= 280, f"Chart container height {box['height']}px exceeds 280px limit."


def test_provenance_drawer_unification(page, base_url):
    prime_page(page)
    goto_workbench(page, base_url)
    drawer = page.locator(PROVENANCE_PANEL)
    expect(drawer).to_be_attached()
