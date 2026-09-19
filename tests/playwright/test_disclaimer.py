"""First-compile disclaimer gate on /app."""

from __future__ import annotations

import pytest

from tests.playwright.helpers import COMPILE_BTN, DESKTOP_VIEWPORT, app_url

pytestmark = pytest.mark.playwright

GATE = "#disclaimer-gate"
ACK = "#disclaimer-ack"


def test_disclaimer_gate_requires_ack(page, base_url):
    page.set_viewport_size(DESKTOP_VIEWPORT)
    page.add_init_script(
        "try { localStorage.setItem('assure_onboarding_complete', '1'); } catch (e) {}"
    )
    page.goto(app_url(base_url), wait_until="domcontentloaded")
    page.wait_for_selector(GATE, state="visible")
    page.locator(ACK).click()
    page.wait_for_function(
        "() => { var g = document.getElementById('disclaimer-gate'); return !g || g.hidden; }"
    )
    page.wait_for_selector(COMPILE_BTN, state="visible")
    acked = page.evaluate("() => localStorage.getItem('assure_disclaimer_ack')")
    assert acked == "1"
