"""Workspace create flow — lazy persist until Create Workspace."""

from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.playwright


def _open_projects(page) -> None:
    page.evaluate(
        "() => window.AssureNav && window.AssureNav.switchView('projects', {replaceHash: false, persist: false})"
    )
    page.wait_for_selector("#panel-write", state="visible")


def test_wizard_creates_project(page, base_url):
    from tests.playwright.helpers import prime_page

    prime_page(page)
    page.goto(f"{base_url.rstrip('/')}/app", wait_until="domcontentloaded")
    page.wait_for_function(
        """() => window.AssureNav && window.AssureProjects && typeof window.AssureProjects.beginCreate === 'function'""",
        timeout=30_000,
    )
    _open_projects(page)
    page.wait_for_selector("#projects-new-btn", state="visible")
    page.locator("#projects-new-btn").click()
    page.wait_for_selector("#workspace-canvas-create", state="visible")
    page.locator('[data-create-template="blank"]').click()

    title = f"PW Wizard {int(time.time())}"
    page.locator("#workspace-create-title").fill(title)

    with page.expect_response(
        lambda r: r.url.rstrip("/").endswith("/api/projects") and r.request.method == "POST",
        timeout=30_000,
    ) as resp_info:
        page.locator("#workspace-create-confirm").click()
    response = resp_info.value
    assert response.status in (200, 201)
    body = response.json()
    assert body.get("ok") is True
    project_id = body["id"]

    page.wait_for_function(
        "(pid) => window.AssureProjects && window.AssureProjects.selectedId === pid",
        arg=project_id,
        timeout=15_000,
    )
