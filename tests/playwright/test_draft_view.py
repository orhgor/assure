"""Draft / generate view must show the compile UI above shared helper panes."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.playwright.helpers import goto_workbench, prime_page

pytestmark = pytest.mark.playwright


def test_draft_view_has_compile_ui(page: Page, base_url: str):
    prime_page(page)
    goto_workbench(page, base_url)
    page.goto(
        f"{base_url.rstrip('/')}/app?lang=en&view=generate",
        wait_until="domcontentloaded",
    )
    page.wait_for_selector("#generate-compile-btn", state="visible")

    expect(page.locator("#view-generate")).to_be_visible()
    expect(page.locator("#generate-intent")).to_be_visible()
    expect(page.locator("#generate-compile-btn")).to_be_visible()
    expect(page.locator(".stepper-timeline")).to_be_visible()
    expect(page.locator("#generate-full-audit-btn")).to_be_visible()

    for panel_id in (
        "substrate-vault",
        "argument-spine",
        "document-structure",
        "refine-workspace",
    ):
        expect(page.locator(f"#{panel_id}")).not_to_have_attribute("open", "")

    first_shared = page.locator("#left-pane-shared details").first
    expect(first_shared).not_to_have_attribute("open", "")
