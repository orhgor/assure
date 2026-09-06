"""Docked confidence overlay toggles on/off after Full Audit."""

from __future__ import annotations

import pytest

from tests.playwright.helpers import (
    COMPILE_INPUT,
    FULL_AUDIT_BTN,
    CONFIDENCE_TOGGLE,
    CONFIDENCE_WRAP,
    RENDER_TARGET,
    click_workbench,
    confidence_spans_sample,
    dock_document,
    route_draft_success,
    route_redhat_success,
    wait_audit_complete,
    wait_compile_ready,
)

pytestmark = pytest.mark.playwright


def test_confidence_overlay_toggles_off(workbench_page):
    page = workbench_page
    spans = confidence_spans_sample()

    def draft_with_spans(route):
        route_draft_success(route, confidence_spans=spans)

    page.route("**/draft/stream", draft_with_spans)
    page.route("**/draft/redhat/stream", route_redhat_success)

    page.locator(COMPILE_INPUT).fill(
        "Summarize humanitarian logistics best practices in three bullet points."
    )
    click_workbench(page, FULL_AUDIT_BTN)
    wait_compile_ready(page)
    wait_audit_complete(page, timeout_ms=60_000)
    dock_document(page)

    page.wait_for_selector(CONFIDENCE_WRAP, state="visible", timeout=30_000)
    assert page.locator(CONFIDENCE_TOGGLE).is_checked()

    page.locator(CONFIDENCE_TOGGLE).click()
    page.wait_for_selector(f"{RENDER_TARGET}.confidence-overlay-off", state="attached")
    assert page.locator(CONFIDENCE_TOGGLE).is_checked() is False

    page.locator(CONFIDENCE_TOGGLE).click()
    overlay_off = page.locator(f"{RENDER_TARGET}.confidence-overlay-off").count()
    assert overlay_off == 0
