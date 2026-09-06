"""Reasoning graph drawer toggle and render."""

from __future__ import annotations

import pytest

from tests.playwright.helpers import (
    REASONING_GRAPH_BTN,
    REASONING_GRAPH_DRAWER,
    dock_document,
    fill_and_compile,
    route_draft_success,
    wait_compile_ready,
)

pytestmark = pytest.mark.playwright


def test_reasoning_graph_renders_and_toggles(workbench_page):
    page = workbench_page
    page.route("**/draft/stream", route_draft_success)

    fill_and_compile(page, "Outline a brief compliance memo.")
    wait_compile_ready(page)
    dock_document(page)

    drawer = page.locator(REASONING_GRAPH_DRAWER)
    assert drawer.is_hidden()

    page.locator(REASONING_GRAPH_BTN).click()
    page.wait_for_function(
        f"""() => {{
          const d = document.querySelector('{REASONING_GRAPH_DRAWER}');
          return d && !d.hidden;
        }}""",
        timeout=10_000,
    )

    page.wait_for_function(
        """() => {
          const cy = document.querySelector('#reasoning-graph-cy');
          if (!cy) return false;
          return cy.querySelector('canvas') || cy.querySelector('.reasoning-graph-fallback li');
        }""",
        timeout=15_000,
    )

    page.locator("#reasoning-graph-close").click()
    page.wait_for_function(
        f"""() => {{
          const d = document.querySelector('{REASONING_GRAPH_DRAWER}');
          return d && d.hidden;
        }}""",
        timeout=10_000,
    )
