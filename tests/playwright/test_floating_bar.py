"""Floating action bar — surgical toolbar on paragraph nodes."""

from __future__ import annotations

import pytest

from tests.playwright.helpers import (
    NODE_ID,
    REFINE_FORM,
    dock_document,
    fill_and_compile,
    route_draft_success,
    wait_compile_ready,
)

pytestmark = pytest.mark.playwright

FLOATING_BAR = "#jdf-floating-bar"


def test_floating_bar_shows_and_opens_refine(workbench_page):
    page = workbench_page
    page.route("**/draft/stream", route_draft_success)

    fill_and_compile(page, "Write about supply chain resilience.")
    wait_compile_ready(page)
    dock_document(page)

    page.wait_for_function(
        f"""() => document.querySelector('[data-node-id="{NODE_ID}"]') !== null"""
    )
    page.evaluate(
        """(nodeId) => {
          const el =
            document.querySelector('[data-node-id="' + nodeId + '"]') ||
            document.querySelector('.jdf-node') ||
            document.querySelector('.jdf-tiptap-host');
          if (window.AssureSurgicalClick && el) {
            window.AssureSurgicalClick.showFloatingBar(nodeId, el);
          }
        }""",
        NODE_ID,
    )
    page.wait_for_function(
        f"""() => {{
          const bar = document.querySelector('{FLOATING_BAR}');
          return bar && !bar.hasAttribute('hidden');
        }}""",
        timeout=10_000,
    )

    page.locator("#floating-rewrite-btn").click(force=True)
    page.wait_for_selector(REFINE_FORM, state="visible", timeout=10_000)
