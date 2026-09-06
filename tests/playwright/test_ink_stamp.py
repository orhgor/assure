"""Physical ink-stamp badges on verified nodes."""

from __future__ import annotations

import pytest

from tests.playwright.helpers import (
    INK_STAMP,
    dock_document,
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
    dock_document(page)

    page.wait_for_selector(INK_STAMP, timeout=15_000)
    stamp = page.locator(INK_STAMP).first
    text = stamp.inner_text().upper()
    assert "VERIFIED" in text or "Z3" in text or "AUDIT" in text
