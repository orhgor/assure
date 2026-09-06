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


def test_lock_ui(page, base_url, project_id: str = "default"):
    """Lock via API, reload, then assert banner and disabled edit controls."""
    from tests.playwright.helpers import prime_page

    prime_page(page)
    root = base_url.rstrip("/")
    page.goto(f"{root}/app", wait_until="domcontentloaded")
    page.wait_for_selector("#jdf-render-target", timeout=10_000)

    lock_response = page.request.post(f"{root}/api/projects/{project_id}/lock")
    assert lock_response.status == 200

    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#jdf-render-target", timeout=10_000)
    page.wait_for_function(
        """() => {
          var banner = document.getElementById('compliance-lock-banner');
          return banner && !banner.hidden && document.body.classList.contains('assure-doc-locked');
        }""",
        timeout=10_000,
    )

    banner = page.locator("#compliance-lock-banner")
    assert banner.is_visible()
    assert page.evaluate("() => document.body.classList.contains('assure-doc-locked')")

    assert page.locator("#compliance-lock-btn").is_disabled()
    assert page.locator("#save-status").is_disabled()
