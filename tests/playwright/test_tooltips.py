"""Major workbench actions expose tooltips."""

from __future__ import annotations

import pytest

from tests.playwright.helpers import COMPILE_BTN, goto_workbench, prime_page

pytestmark = pytest.mark.playwright


def test_tooltips_on_primary_actions(page, base_url):
    prime_page(page)
    goto_workbench(page, base_url)
    compile_btn = page.locator(COMPILE_BTN)
    assert compile_btn.is_visible()
    tooltip = compile_btn.get_attribute("data-tooltip") or compile_btn.get_attribute(
        "data-i18n-tooltip"
    )
    assert tooltip
    classes = compile_btn.get_attribute("class") or ""
    assert "tooltip-trigger" in classes
