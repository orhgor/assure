"""Lock button UI — deterministic data-state harness."""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.playwright


def _fresh_project(page: Page, base_url: str) -> str:
    root = base_url.rstrip("/")
    created = page.request.post(
        f"{root}/api/projects",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"title": "Lock UI Test"}),
    )
    assert created.status in (200, 201)
    project_id = created.json()["id"]
    page.goto(f"{root}/app?project={project_id}", wait_until="domcontentloaded")
    page.wait_for_function("() => window.AssureNav && window.AssureProjects")
    from tests.playwright.helpers import enter_compiler

    enter_compiler(page, project_id)
    page.evaluate(
        """(pid) => {
          window.__ASSURE_PROJECT_ID__ = pid;
          if (window.AssureCompliance && window.AssureCompliance.refreshLockState) {
            window.AssureCompliance.refreshLockState();
          }
        }""",
        project_id,
    )
    return project_id


def test_lock_button_and_banner(page: Page, base_url: str):
    from tests.playwright.helpers import prime_page

    prime_page(page)
    _fresh_project(page, base_url)
    page.wait_for_selector("#workbench-root", timeout=10_000)
    page.wait_for_selector("#compliance-lock-btn", state="visible")
    expect(page.locator("#compliance-lock-banner")).to_be_hidden()
    expect(page.locator("#workbench-root")).to_have_attribute("data-state", "idle")


def test_lock_document(page: Page, base_url: str):
    """Lock via API, wait for data-state=locked, assert banner and disabled edits."""
    from tests.playwright.helpers import prime_page

    prime_page(page)
    project_id = _fresh_project(page, base_url)
    page.wait_for_selector("#workbench-root", timeout=10_000)
    page.wait_for_selector("#jdf-render-target", timeout=10_000)

    root = base_url.rstrip("/")
    lock_response = page.request.post(f"{root}/api/projects/{project_id}/lock")
    assert lock_response.status == 200

    page.reload(wait_until="domcontentloaded")
    from tests.playwright.helpers import enter_compiler

    enter_compiler(page, project_id)
    page.evaluate(
        """(pid) => { window.__ASSURE_PROJECT_ID__ = pid; }""",
        project_id,
    )
    page.wait_for_selector("#workbench-root[data-state='locked']", timeout=7_000)

    expect(page.locator("#compliance-lock-banner")).to_be_visible(timeout=5_000)
    expect(page.locator("#compliance-lock-btn")).to_be_disabled()
    expect(page.locator("#save-status")).to_be_disabled()
