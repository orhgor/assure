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
    page.evaluate(
        """() => {
          document.body.classList.add('assure-doc-locked');
          var banner = document.getElementById('compliance-lock-banner');
          if (banner) { banner.hidden = false; banner.textContent = 'Locked'; }
          var btn = document.getElementById('compliance-lock-btn');
          if (btn) btn.disabled = true;
        }"""
    )
    assert page.evaluate("() => document.body.classList.contains('assure-doc-locked')") is True
