"""Day 1 gate — frozen 3-pane founder shell layout contract."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.playwright.helpers import goto_founder_workbench

pytestmark = [pytest.mark.quality_check, pytest.mark.playwright]

VIEWPORTS = [(1280, 800), (1024, 768), (1920, 1080)]


def _rail_width(page: Page) -> float:
    box = page.locator("#state-rail").bounding_box()
    assert box is not None, "state-rail has no bounding box"
    return box["width"]


def test_frozen_shell_state_rail_48px(page: Page, base_url: str):
    goto_founder_workbench(page, base_url)
    expect(page.locator("#state-rail")).to_be_visible()
    width = _rail_width(page)
    assert width == pytest.approx(48, abs=1), f"state-rail must be 48px, got {width}"


@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_frozen_shell_survives_resize(page: Page, base_url: str, width: int, height: int):
    goto_founder_workbench(page, base_url)
    page.set_viewport_size({"width": width, "height": height})

    rail = page.locator("#state-rail")
    left = page.locator(".left-pane")
    editor = page.locator("#founder-draft-editor")

    expect(rail).to_be_visible()
    expect(left).to_be_visible()
    expect(editor).to_be_visible()

    assert _rail_width(page) == pytest.approx(48, abs=1)

    rail_box = rail.bounding_box()
    left_box = left.bounding_box()
    editor_box = editor.bounding_box()
    assert rail_box and left_box and editor_box

    # Left pane starts after rail; editor after left pane (no horizontal overlap).
    assert left_box["x"] >= rail_box["x"] + rail_box["width"] - 1
    assert editor_box["x"] >= left_box["x"] + left_box["width"] - 1


def test_frozen_shell_main_document_in_canvas(page: Page, base_url: str):
    goto_founder_workbench(page, base_url)
    expect(page.locator("body.founder-workbench:not(.legacy-workbench)")).to_be_visible()
    expect(page.locator("#assure-app.founder-shell-layout")).to_be_visible()
    expect(page.locator("#founder-draft-editor")).to_be_visible()
