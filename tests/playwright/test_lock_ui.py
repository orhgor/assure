"""Lock button UI — edit disabled when locked."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.playwright


def test_lock_button_and_banner(page, base_url):
    from tests.playwright.helpers import prime_page

    prime_page(page)
    page.goto(f"{base_url.rstrip('/')}/app", wait_until="domcontentloaded")
    page.wait_for_selector("#compliance-lock-btn", state="visible")
    assert page.locator("#compliance-lock-banner").is_hidden()

    page.evaluate(
        """() => {
          window.__ASSURE_PROJECT_ID__ = 'default';
          if (window.AssureCompliance) window.AssureCompliance.refreshLockState();
        }"""
    )
    page.wait_for_timeout(300)
    assert page.locator("#compliance-lock-btn").is_visible()


def test_locked_disables_edit_controls(page, base_url):
    from tests.playwright.helpers import prime_page

    prime_page(page)
    page.goto(f"{base_url.rstrip('/')}/app", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => window.AssureCompliance && window.AssureCompliance.refreshLockState"
    )
    page.evaluate("() => { window.__ASSURE_PROJECT_ID__ = 'default'; }")
    lock_res = page.request.post(f"{base_url.rstrip('/')}/api/projects/default/lock")
    assert lock_res.status == 200
    page.evaluate("() => window.AssureCompliance.refreshLockState()")
    page.wait_for_function("() => document.body.classList.contains('assure-doc-locked')")
    assert page.locator("#compliance-lock-btn").is_disabled()
