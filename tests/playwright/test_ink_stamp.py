"""Physical ink-stamp badges on verified nodes."""

from __future__ import annotations

import pytest

from tests.playwright.helpers import (
    INK_STAMP,
    fill_and_compile,
    route_draft_success,
    wait_compile_ready,
)

pytestmark = pytest.mark.playwright


def test_ink_stamp_appears_on_verified_nodes(workbench_page):
    page = workbench_page
    page.route("**/draft/stream", route_draft_success)

    fill_and_compile(page, "Write a short paragraph about revenue growth.")
    wait_compile_ready(page)

    page.wait_for_function(
        """() => {
          if (window.AssureWowEffects && typeof window.AssureWowEffects.refreshStamps === 'function') {
            window.AssureWowEffects.refreshStamps();
          }
          return document.querySelectorAll('.ink-stamp').length > 0;
        }""",
        timeout=20_000,
    )
    stamp = page.locator(INK_STAMP).first
    text = stamp.inner_text().upper()
    assert "VERIFIED" in text or "Z3" in text or "AUDIT" in text
