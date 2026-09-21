"""Founder right drawer — Evidence, Red-Hat, Export."""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page, expect

from tests.playwright.helpers import goto_founder_workbench
from tests.playwright.test_founder_workbench import SAMPLE_RUN, _dismiss_overlays, _mock_runs_api

pytestmark = pytest.mark.playwright

MOCK_EVIDENCE = {
    "ok": True,
    "lock_hash": "abc123hash4567",
    "source_id": "sub-pw-1",
    "source_name": "NAIC_Underwriting_Policy_2025.pdf",
    "page_number": 1,
    "excerpt": "Revenue reached $12M in Q3 per policy.",
    "z3_proof": "(assert (= Revenue 12000000))",
}


def test_evidence_drawer_renders(page: Page, base_url: str):
    page.route(
        "**/api/locks/*/evidence",
        lambda route: route.fulfill(
            content_type="application/json", body=json.dumps(MOCK_EVIDENCE)
        ),
    )
    goto_founder_workbench(page, base_url)
    page.evaluate(
        """() => {
          document.dispatchEvent(new CustomEvent('assure:lock-pill-click', {
            detail: { lockHash: 'abc123hash4567' },
          }));
        }"""
    )
    expect(page.locator("#workbench-right-drawer")).to_be_visible(timeout=5_000)
    expect(page.locator("#drawer-evidence")).to_be_visible()
    expect(page.locator(".drawer-evidence-excerpt")).to_contain_text("Revenue reached")
    expect(page.locator(".drawer-evidence-z3")).to_contain_text("assert")


def test_redhat_drawer_renders_findings(page: Page, base_url: str):
    runs = [
        {
            **SAMPLE_RUN,
            "redhat_findings": [
                {
                    "id": "rh_pw_drawer",
                    "run_id": SAMPLE_RUN["id"],
                    "title": "Missing citation",
                    "content": "Revenue lacks a source citation.",
                    "severity": "high",
                    "suggested_fix": "Add NAIC policy citation.",
                    "status": "open",
                }
            ],
        }
    ]

    page.route(
        "**/api/runs**",
        lambda route: route.fulfill(
            content_type="application/json",
            body=json.dumps({"ok": True, "runs": runs, "count": 1}),
        )
        if route.request.method == "GET"
        else route.continue_(),
    )
    goto_founder_workbench(page, base_url)
    expect(page.locator(".run-card")).to_have_count(1, timeout=15_000)
    page.locator('[data-action="redhat"]').first.click(force=True)
    expect(page.locator("#workbench-right-drawer")).to_be_visible(timeout=5_000)
    expect(page.locator("#drawer-redhat")).to_be_visible()
    expect(page.locator(".drawer-redhat-card")).to_contain_text("Missing citation")
    expect(page.locator(".drawer-redhat-card")).to_contain_text("Add NAIC policy citation")


def test_export_pdf_saves_before_redirect(page: Page, base_url: str):
    _mock_runs_api(page)
    goto_founder_workbench(page, base_url)
    page.wait_for_function(
        "() => document.querySelector('#founder-draft-editor .ProseMirror')",
        timeout=15_000,
    )
    editor = page.locator("#founder-draft-editor .ProseMirror")
    editor.click()
    editor.type("Export me before PDF.")

    save_calls: list[str] = []

    def handle_draft_save(route):
        if route.request.method == "POST":
            save_calls.append(route.request.url)
            route.fulfill(
                content_type="application/json", body=json.dumps({"ok": True, "document": {}})
            )
            return
        route.continue_()

    page.route("**/api/projects/founder/draft", handle_draft_save)

    page.evaluate("() => window.AssureExportDrawer && window.AssureExportDrawer.open()")
    expect(page.locator("#drawer-export")).to_be_visible(timeout=5_000)

    with page.expect_request("**/api/projects/founder/draft") as save_req:
        with page.expect_request("**/export?format=pdf") as _pdf_req:
            page.locator(".drawer-export-pdf").click()
    assert save_req.value.method == "POST"
    assert save_calls


def test_export_jdf_download(page: Page, base_url: str):
    goto_founder_workbench(page, base_url)
    page.wait_for_function(
        "() => document.querySelector('#founder-draft-editor .ProseMirror')",
        timeout=15_000,
    )
    page.evaluate("() => window.AssureExportDrawer && window.AssureExportDrawer.open()")
    expect(page.locator("#drawer-export")).to_be_visible(timeout=5_000)

    with page.expect_download() as download_info:
        page.locator(".drawer-export-jdf").click()
    download = download_info.value
    assert download.suggested_filename == "assure_draft.jdf"
