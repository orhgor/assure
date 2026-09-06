"""Draft / generate view must show the compile UI above shared helper panes."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.playwright.helpers import goto_workbench, prime_page

pytestmark = pytest.mark.playwright


def test_draft_view_has_compile_ui(page: Page, base_url: str):
    prime_page(page)
    page.add_init_script(
        """
        try {
          localStorage.setItem('assure_view', 'vault');
          localStorage.setItem('assure_tool', 'library');
          localStorage.setItem('assure_vault_collapsed', '0');
          localStorage.setItem('assure_spine_collapsed', '0');
        } catch (e) {}
        """
    )
    page.goto(
        f"{base_url.rstrip('/')}/app?lang=en&view=generate",
        wait_until="domcontentloaded",
    )
    page.wait_for_selector("#generate-compile-btn", state="visible")

    expect(page.locator("#view-generate")).to_be_visible()
    intent = page.locator("#generate-intent")
    expect(intent).to_be_visible()
    assemble = page.locator("#generate-compile-btn")
    expect(assemble).to_be_visible()
    expect(page.locator(".stepper-timeline")).to_be_visible()
    expect(page.locator("#generate-full-audit-btn")).to_be_visible()

    intent_box = intent.bounding_box()
    assemble_box = assemble.bounding_box()
    assert intent_box is not None and intent_box["height"] > 20
    assert assemble_box is not None and assemble_box["height"] > 16

    page.wait_for_function(
        "() => !document.getElementById('substrate-vault')?.open",
        timeout=5_000,
    )

    for panel_id in (
        "substrate-vault",
        "argument-spine",
        "document-structure",
        "refine-workspace",
    ):
        expect(page.locator(f"#{panel_id}")).not_to_have_attribute("open", "")


def test_draft_view_compile_ui_after_workbench_prime(page: Page, base_url: str):
    prime_page(page)
    goto_workbench(page, base_url)
    expect(page.locator("#view-generate")).to_be_visible()
    expect(page.locator("#generate-intent")).to_be_visible()
    expect(page.locator("#generate-compile-btn")).to_be_visible()
    expect(page.locator(".stepper-timeline")).to_be_visible()
