"""Sign-off panel UI tests."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.playwright


def _open_more_panel(page):
    page.locator("#command-deck-more summary").click()
    page.wait_for_timeout(200)


def test_signoff_panel_opens(page, base_url):
    from tests.playwright.helpers import prime_page

    prime_page(page)
    page.goto(f"{base_url.rstrip('/')}/app", wait_until="domcontentloaded")
    _open_more_panel(page)
    page.wait_for_selector("#signoff-open-btn", state="visible")
    page.locator("#signoff-open-btn").click()
    page.wait_for_selector("#signoff-panel", state="visible")
    assert page.locator("#signoff-approve-btn").is_visible()
    assert page.locator("#signoff-reject-btn").is_visible()


def test_signoff_form_fields(page, base_url):
    from tests.playwright.helpers import prime_page

    prime_page(page)
    page.goto(f"{base_url.rstrip('/')}/app", wait_until="domcontentloaded")
    _open_more_panel(page)
    page.locator("#signoff-open-btn").click()
    page.locator("#signoff-reviewer-name").fill("Reviewer One")
    page.locator("#signoff-comment").fill("Approved for release")
    assert page.locator("#signoff-reviewer-name").input_value() == "Reviewer One"
