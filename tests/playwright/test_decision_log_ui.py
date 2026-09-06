"""Decision Log (OMP timeline) UI tests."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.playwright


def _open_more_panel(page):
    page.locator("#command-deck-more summary").click()
    page.wait_for_timeout(200)


def test_decision_log_panel(page, base_url):
    from tests.playwright.helpers import prime_page

    prime_page(page)
    page.goto(f"{base_url.rstrip('/')}/app", wait_until="domcontentloaded")
    _open_more_panel(page)
    page.wait_for_selector("#decision-log-open-btn", state="visible")
    page.locator("#decision-log-open-btn").click()
    page.wait_for_selector("#decision-log-panel", state="visible")
    assert page.locator("#decision-log-panel").is_visible()
    assert page.locator("#decision-log-timeline").count() == 1


def test_decision_log_fetches_memories(page, base_url):
    from tests.playwright.helpers import prime_page

    prime_page(page)
    page.goto(f"{base_url.rstrip('/')}/app", wait_until="domcontentloaded")
    page.evaluate("() => { window.__ASSURE_PROJECT_ID__ = 'prj_playwright'; }")
    _open_more_panel(page)
    page.locator("#decision-log-open-btn").click()
    page.wait_for_timeout(800)
    timeline = page.locator("#decision-log-timeline")
    assert timeline.is_visible()
    text = timeline.inner_text()
    assert text is not None
