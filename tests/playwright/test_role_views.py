"""Role-based default workbench views."""

from __future__ import annotations

import pytest

from tests.playwright.helpers import ROLE_SWITCHER, goto_workbench, prime_page

pytestmark = pytest.mark.playwright


def test_role_views_executive_hides_draft_tools(page, base_url):
    prime_page(page)
    goto_workbench(page, base_url)
    page.select_option(ROLE_SWITCHER, "executive")
    page.wait_for_function(
        """() => document.querySelectorAll('.app-sidebar-link[data-tool="surgical"]').length === 0""",
        timeout=15_000,
    )
    assert page.locator('.app-sidebar-link[data-tool="surgical"]').count() == 0


def test_role_views_compliance_shows_analytics(page, base_url):
    prime_page(page)
    goto_workbench(page, base_url)
    page.select_option(ROLE_SWITCHER, "compliance")
    page.wait_for_function(
        """() => {
          const btn = document.querySelector('.app-sidebar-link[data-tool="analytics"]');
          return btn && !btn.hidden;
        }""",
        timeout=15_000,
    )
    analytics = page.locator('.app-sidebar-link[data-tool="analytics"]')
    assert analytics.is_visible()
