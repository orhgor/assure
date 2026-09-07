"""v2.1 wow polish: Inter, stepper items, hidden stamps, coachmark tour."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.playwright.helpers import goto_workbench, prime_page

pytestmark = pytest.mark.playwright


def test_design_system(page: Page, base_url: str):
    prime_page(page)
    goto_workbench(page, base_url)
    font_family = page.evaluate("() => getComputedStyle(document.body).fontFamily")
    assert "Inter" in font_family, f"Expected Inter font, got {font_family}"
    trust = page.evaluate(
        """() => getComputedStyle(document.documentElement).getPropertyValue('--color-trust').trim()"""
    )
    assert trust


def test_stepper_connectors(page: Page, base_url: str):
    prime_page(page)
    goto_workbench(page, base_url)
    connector = page.locator(".step-item:not(:last-child)").first
    expect(connector).to_be_visible()
    expect(page.locator(".step-connector").first).to_be_visible()


def test_trust_hierarchy(page: Page, base_url: str):
    prime_page(page)
    goto_workbench(page, base_url)
    stamps = page.locator(".ink-stamp")
    assert stamps.count() == 0, "Ink stamps should be hidden in unified trust hierarchy."


def test_onboarding_tour(page: Page, base_url: str):
    page.set_viewport_size({"width": 1440, "height": 900})
    page.add_init_script(
        """
        try {
          localStorage.removeItem('assure_onboarding_complete');
          sessionStorage.setItem('assure_session_compiles', '0');
          window.__ASSURE_WOW_EFFECTS__ = true;
        } catch (e) {}
        """
    )
    page.goto(
        f"{base_url.rstrip('/')}/app?lang=en&view=generate&onboarding=true",
        wait_until="domcontentloaded",
    )
    page.wait_for_selector("#generate-compile-btn", state="visible")
    tour = page.locator(".coachmark-tour").first
    expect(tour).to_be_visible(timeout=15_000)


def test_lucide_sidebar_mapping(page: Page, base_url: str):
    prime_page(page)
    goto_workbench(page, base_url)
    expect(page.locator('.app-sidebar-icon [data-lucide="pencil"]')).to_be_visible()
    expect(page.locator('.app-sidebar-icon [data-lucide="file-text"]')).to_be_visible()
    expect(page.locator('.app-sidebar-icon [data-lucide="folder"]')).to_be_visible()
    expect(page.locator('.app-sidebar-icon [data-lucide="bar-chart-2"]')).to_be_visible()
