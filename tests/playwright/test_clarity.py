"""Progressive disclosure hides advanced clutter by default."""

from __future__ import annotations

import pytest

from tests.playwright.helpers import goto_workbench, prime_page

pytestmark = pytest.mark.playwright


def test_advanced_quick_actions_hidden_by_default(page, base_url):
    prime_page(page)
    goto_workbench(page, base_url)
    quick = page.locator("#generate-quick-actions")
    assert quick.is_hidden()

    page.locator("#workbench-advanced-toggle-btn").click()
    page.wait_for_function(
        "() => document.body.classList.contains('workbench-advanced-on')",
        timeout=5_000,
    )
    assert quick.is_visible()


def test_legacy_compile_row_removed(page, base_url):
    prime_page(page)
    goto_workbench(page, base_url)
    assert page.locator("#generate-compile-btn-legacy").count() == 0
