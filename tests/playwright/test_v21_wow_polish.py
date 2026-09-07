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
    header = page.evaluate(
        """() => {
          const h = document.querySelector('.app-header');
          const s = getComputedStyle(h);
          const path = document.querySelector('.app-logo-mark path');
          return {
            bg: s.backgroundColor,
            color: s.color,
            stroke: path && path.getAttribute('stroke'),
          };
        }"""
    )
    assert "255, 255, 255" in header["bg"] or header["bg"] in ("#ffffff", "rgb(255, 255, 255)")
    assert header["stroke"] == "#1A4B8C"


def test_stepper_connectors(page: Page, base_url: str):
    prime_page(page)
    goto_workbench(page, base_url)
    expect(page.locator(".step-item").first).to_be_visible()
    assert page.locator(".step-item").count() == 4
    display = page.evaluate(
        """() => getComputedStyle(document.querySelector('.stepper-timeline')).display"""
    )
    assert display == "flex"
    assert page.locator(".step-connector").count() == 3


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
    expect(page.locator('.app-sidebar-icon [data-lucide="folder"]')).to_be_visible()
    expect(page.locator('.app-sidebar-icon [data-lucide="bar-chart-2"]')).to_be_visible()


def test_laser_beam_grows_per_segment(workbench_page):
    page = workbench_page
    heights = page.evaluate(
        """() => {
          const host = document.querySelector('#jdf-render-target');
          if (!host || !window.AssureWowEffects) return [];
          host.style.position = 'relative';
          host.style.minHeight = '240px';
          const mk = (top) => {
            const el = document.createElement('article');
            el.className = 'jdf-node';
            el.style.position = 'absolute';
            el.style.left = '0';
            el.style.right = '0';
            el.style.top = top + 'px';
            el.style.height = '72px';
            host.appendChild(el);
            return el;
          };
          const a = mk(8);
          const b = mk(96);
          const beamFn = window.AssureWowEffects.extendLaserBeamToNode || window.AssureWowEffects.progressToNode;
          beamFn.call(window.AssureWowEffects, a);
          const h1 = parseFloat((document.getElementById('laser-beam') || {}).style.height || '0');
          beamFn.call(window.AssureWowEffects, b);
          const h2 = parseFloat((document.getElementById('laser-beam') || {}).style.height || '0');
          return [h1, h2];
        }"""
    )
    assert len(heights) == 2
    assert heights[1] > heights[0]
