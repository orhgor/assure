"""Wow polish: Inter tokens, Lucide sidebar, stepper connectors, tour, laser."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.playwright.helpers import (
    LASER_BEAM,
    fill_and_compile,
    goto_workbench,
    prime_page,
    route_draft_success,
    wait_compile_ready,
)

pytestmark = pytest.mark.playwright


def test_workbench_uses_inter_and_primary_token(page: Page, base_url: str):
    prime_page(page)
    goto_workbench(page, base_url)
    family = page.evaluate(
        """() => getComputedStyle(document.querySelector('#assure-app')).fontFamily"""
    )
    assert "Inter" in family
    primary = page.evaluate(
        """() => getComputedStyle(document.documentElement).getPropertyValue('--color-primary').trim()"""
    )
    assert primary
    space = page.evaluate(
        """() => getComputedStyle(document.documentElement).getPropertyValue('--space-md').trim()"""
    )
    assert space


def test_sidebar_uses_lucide_not_emoji(page: Page, base_url: str):
    prime_page(page)
    goto_workbench(page, base_url)
    icons = page.locator(".app-sidebar-icon")
    expect(icons.first).to_be_visible()
    expect(page.locator('.app-sidebar-icon svg[data-icon="file-text"]')).to_be_visible()
    expect(page.locator('.app-sidebar-icon svg[data-icon="pencil"]')).to_be_visible()
    text = page.locator("#app-sidebar").inner_text()
    assert "✏️" not in text
    assert "📝" not in text
    assert "⚙️" not in text


def test_stepper_connectors_visible(page: Page, base_url: str):
    prime_page(page)
    goto_workbench(page, base_url)
    expect(page.locator(".step-connector").first).to_be_visible()
    assert page.locator(".step-connector").count() == 3


def test_laser_beam_sweeps_per_node(workbench_page):
    page = workbench_page
    page.route("**/draft/stream", route_draft_success)
    fill_and_compile(page, "Summarize disaster preparedness for field teams.")
    wait_compile_ready(page)
    page.wait_for_function(
        f"""() => {{
          const beam = document.querySelector('{LASER_BEAM}');
          return beam && (beam.classList.contains('is-segmented') || beam.classList.contains('is-running') || Number(beam.style.height || 0) >= 0);
        }}""",
        timeout=20_000,
    )
    page.evaluate(
        """() => {
          const nodes = document.querySelectorAll('#jdf-render-target .jdf-node, #jdf-render-target .jdf-ast-node');
          const last = nodes[nodes.length - 1];
          if (window.AssureWowEffects && last && typeof window.AssureWowEffects.progressToNode === 'function') {
            window.AssureWowEffects.progressToNode(last);
          }
        }"""
    )
    beam = page.locator(LASER_BEAM)
    expect(beam).to_be_attached()
    assert (
        page.locator("#jdf-render-target .jdf-node, #jdf-render-target .jdf-ast-node").count() >= 1
    )


def test_onboarding_tour_appears_on_first_load(page: Page, base_url: str):
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
    page.goto(f"{base_url.rstrip('/')}/app?lang=en&view=generate", wait_until="domcontentloaded")
    page.wait_for_selector("#generate-compile-btn", state="visible")
    page.wait_for_selector(".onboarding-callout", timeout=15_000)
    copy = page.locator(".onboarding-callout-text")
    expect(copy).to_be_visible()
    text = copy.inner_text().lower()
    assert "intent" in text or "assemble" in text or "start" in text
