"""Founder left pane — Sources, Versions, and Restore wiring."""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page, expect

from tests.playwright.helpers import goto_founder_workbench

pytestmark = pytest.mark.playwright

MOCK_SUBSTRATE = {
    "ok": True,
    "files": [
        {
            "id": "sub-pw-left-1",
            "filename": "brief.pdf",
            "file_size_bytes": 4096,
            "included": True,
            "claims_count": 2,
        }
    ],
}

MOCK_HISTORY = {
    "ok": True,
    "count": 2,
    "history": [
        {
            "version": 2,
            "timestamp": "2026-01-02 12:00:00",
            "mutation_type": "GENERATE_DOCK",
            "change_summary": "Docked run output",
        },
        {
            "version": 1,
            "timestamp": "2026-01-01 10:00:00",
            "mutation_type": "MANUAL_TOUCHUP",
            "change_summary": "Initial draft",
        },
    ],
}

RESTORED_DOC = {
    "document_id": "doc-founder-restore",
    "meta": {"project_id": "founder"},
    "truth_ledger": {},
    "body": [
        {
            "type": "section",
            "id": "sec-r",
            "title": "Restored",
            "children": [
                {
                    "type": "paragraph",
                    "id": "para-r",
                    "content": "Restored version one paragraph.",
                    "meta": {},
                }
            ],
        }
    ],
}


def _mock_left_pane_apis(page: Page) -> None:
    page.route(
        "**/api/projects/founder/substrate",
        lambda route: route.fulfill(
            content_type="application/json",
            body=json.dumps(MOCK_SUBSTRATE),
        ),
    )
    page.route(
        "**/api/projects/founder/history",
        lambda route: route.fulfill(
            content_type="application/json",
            body=json.dumps(MOCK_HISTORY),
        ),
    )

    def handle_restore(route):
        if route.request.method == "POST":
            route.fulfill(
                content_type="application/json",
                body=json.dumps({"ok": True, "version": 1, "document": RESTORED_DOC}),
            )
            return
        route.continue_()

    page.route("**/api/projects/founder/restore", handle_restore)


def test_sources_tab_renders_files(page: Page, base_url: str):
    _mock_left_pane_apis(page)
    goto_founder_workbench(page, base_url)
    page.locator('.founder-sidebar-tab[data-sidebar-tab="sources"]').click()
    expect(page.locator("#panel-sources")).to_be_visible(timeout=5_000)
    expect(page.locator(".substrate-file-row")).to_have_count(1, timeout=5_000)
    expect(page.locator(".substrate-file-name")).to_contain_text("brief.pdf")


def test_versions_tab_renders_timeline(page: Page, base_url: str):
    _mock_left_pane_apis(page)
    goto_founder_workbench(page, base_url)
    page.locator('.founder-sidebar-tab[data-sidebar-tab="versions"]').click()
    expect(page.locator("#pane-versions")).to_be_visible(timeout=5_000)
    expect(page.locator(".founder-version-entry")).to_have_count(2, timeout=5_000)
    expect(page.locator(".founder-version-entry").first).to_contain_text("v2")
    expect(page.locator(".founder-version-entry").first).to_contain_text("GENERATE_DOCK")
    expect(page.locator(".founder-version-restore")).to_have_count(2)


def test_restore_updates_editor(page: Page, base_url: str):
    _mock_left_pane_apis(page)
    goto_founder_workbench(page, base_url)
    page.wait_for_function(
        "() => document.querySelector('#founder-draft-editor .ProseMirror')",
        timeout=15_000,
    )
    page.locator('.founder-sidebar-tab[data-sidebar-tab="versions"]').click()
    expect(page.locator(".founder-version-restore").last).to_be_visible(timeout=5_000)

    with page.expect_request("**/api/projects/founder/restore") as req_info:
        page.locator(".founder-version-restore").last.click()
    req = req_info.value
    assert req.method == "POST"
    body = json.loads(req.post_data or "{}")
    assert body.get("version") == 1

    editor = page.locator("#founder-draft-editor .ProseMirror")
    expect(editor).to_contain_text("Restored version one paragraph.", timeout=5_000)
