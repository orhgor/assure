"""Role switcher updates workbench chrome."""

from __future__ import annotations

import pytest

from tests.playwright.helpers import ROLE_SWITCHER, goto_workbench, prime_page

pytestmark = pytest.mark.playwright


def test_role_switcher_updates_body_attribute(page, base_url):
    prime_page(page)
    goto_workbench(page, base_url)
    page.select_option(ROLE_SWITCHER, "developer")
    page.wait_for_function(
        """() => document.body.getAttribute('data-workbench-role') === 'developer'""",
        timeout=10_000,
    )
    page.select_option(ROLE_SWITCHER, "admin")
    page.wait_for_function(
        """() => document.body.getAttribute('data-workbench-role') === 'admin'""",
        timeout=10_000,
    )
