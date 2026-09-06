"""Full Audit: compile + Red-Hat completes within 60s."""

from __future__ import annotations

import time

import pytest

from tests.playwright.helpers import (
    COMPILE_INPUT,
    FULL_AUDIT_BTN,
    click_workbench,
    route_draft_success,
    route_redhat_success,
    wait_compile_ready,
)

pytestmark = pytest.mark.playwright


def test_full_audit_completes_within_sixty_seconds(workbench_page):
    page = workbench_page
    page.route("**/draft/stream", route_draft_success)
    page.route("**/draft/redhat/stream", route_redhat_success)

    prompt = "Summarize humanitarian logistics best practices in three bullet points."
    page.locator(COMPILE_INPUT).fill(prompt)
    started = time.monotonic()
    click_workbench(page, FULL_AUDIT_BTN)
    wait_compile_ready(page, timeout_ms=30_000)
    page.wait_for_function(
        "() => window.AssureGenerate && window.AssureGenerate.auditComplete === true",
        timeout=30_000,
    )
    page.wait_for_function(
        "() => window.AssureGenerate && !window.AssureGenerate._redhatPending",
        timeout=30_000,
    )
    elapsed = time.monotonic() - started
    assert elapsed < 60, f"Full audit took {elapsed:.1f}s"
    appendix = page.locator("#jdf-audit-appendix")
    assert appendix.is_visible() or page.evaluate(
        "() => !!(window.AssureGenerate && window.AssureGenerate._auditManifest)"
    )
