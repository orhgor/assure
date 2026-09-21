"""Visual regression snapshots for Operator Cockpit surfaces."""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page

from tests.playwright.helpers import goto_founder_workbench
from tests.playwright.test_founder_workbench import _mock_runs_api
from tests.quality_check.snapshot_utils import assert_screenshot

pytestmark = [pytest.mark.quality_check, pytest.mark.playwright]

EVIDENCE_FIXTURE = {
    "ok": True,
    "lock_hash": "abc123hash4567",
    "source_name": "brief.pdf",
    "source_id": "sub-pw-1",
    "page_number": 1,
    "excerpt": "Revenue reached $12M in Q3 per audited substrate.",
    "z3_proof": "sat\n(model …)\n(check-sat)\n",
}


def test_workbench_snapshot(page: Page, base_url: str):
    _mock_runs_api(page, sse_stream=False)
    goto_founder_workbench(page, base_url)
    page.wait_for_selector("body.founder-workbench")
    page.wait_for_function("() => window.AssureTiptapEditor && window.AssureRunsStack")
    page.wait_for_selector("#founder-draft-editor .ProseMirror")
    page.wait_for_timeout(300)
    assert_screenshot(
        page,
        "workbench-home.png",
        locator=page.locator("#founder-draft-editor"),
        max_diff_pixel_ratio=0.05,
    )


def test_operator_prompt_snapshot(page: Page, base_url: str):
    _mock_runs_api(page, sse_stream=False)
    goto_founder_workbench(page, base_url)
    mod = "Meta+K" if page.evaluate("() => navigator.platform.includes('Mac')") else "Control+K"
    page.keyboard.press(mod)
    page.wait_for_selector("#operator-prompt:not([hidden])")
    assert_screenshot(page, "operator-prompt.png", locator=page.locator("#operator-prompt"))


def test_evidence_drawer_snapshot(page: Page, base_url: str):
    _mock_runs_api(page, sse_stream=False)
    page.route(
        "**/api/locks/*/evidence",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(EVIDENCE_FIXTURE),
        ),
    )
    goto_founder_workbench(page, base_url)
    page.evaluate(
        """() => {
          if (window.openEvidenceDrawer) {
            window.openEvidenceDrawer('abc123hash4567', { preventDefault() {}, stopPropagation() {} });
          }
        }"""
    )
    page.wait_for_selector("#workbench-right-drawer:not([hidden])")
    page.wait_for_selector(".evidence-inspector-section, .drawer-evidence-error", timeout=10_000)
    page.wait_for_timeout(200)
    assert_screenshot(
        page,
        "evidence-drawer.png",
        locator=page.locator("#workbench-right-drawer"),
    )
