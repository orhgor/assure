"""Write / Verify / Audit / Ship action grouping."""

from __future__ import annotations

import pytest

from tests.playwright.helpers import (
    ACTION_PHASE,
    COMPILE_BTN,
    FULL_AUDIT_BTN,
    goto_workbench,
    prime_page,
)

pytestmark = pytest.mark.playwright


def test_action_grouping_phases_present(page, base_url):
    prime_page(page)
    goto_workbench(page, base_url)
    phases = page.locator(ACTION_PHASE)
    assert phases.count() >= 3
    assert page.locator('[data-action-phase="write"]').locator(COMPILE_BTN).is_visible()
    assert page.locator('[data-action-phase="verify"]').locator(FULL_AUDIT_BTN).count() == 1
    assert page.locator('[data-action-phase="verify"]').locator(FULL_AUDIT_BTN).is_hidden()
    assert page.locator('[data-phase="ship"]').is_visible()
