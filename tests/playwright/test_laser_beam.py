"""Live-beam laser compilation wow effect."""

from __future__ import annotations

import pytest

from tests.playwright.helpers import (
    LASER_BEAM,
    fill_and_compile,
    route_draft_success,
    wait_compile_ready,
)

pytestmark = pytest.mark.playwright


def test_laser_beam_appears_and_nodes_animate(workbench_page):
    page = workbench_page
    page.route("**/draft/stream", route_draft_success)

    fill_and_compile(page, "Summarize disaster preparedness for field teams.")
    wait_compile_ready(page)

    page.wait_for_function(
        f"""() => {{
          const beam = document.querySelector('{LASER_BEAM}');
          const nodes = document.querySelectorAll(
            '#jdf-render-target .jdf-node, #jdf-render-target .jdf-ast-node'
          );
          const checks = document.querySelectorAll('.wow-verified-check, .ink-stamp');
          return beam && nodes.length > 0 && checks.length > 0;
        }}""",
        timeout=20_000,
    )
    assert page.locator(LASER_BEAM).count() >= 1
    assert page.locator("#jdf-render-target .jdf-node").count() >= 1
