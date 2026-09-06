"""New project wizard — 3 steps and project creation."""

from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.playwright

WIZARD = "#new-project-wizard"
WIZARD_NEXT = "#wizard-next-btn"
WIZARD_CREATE = "#wizard-create-btn"
WIZARD_TITLE = "#wizard-title-input"
WIZARD_PROMPT = "#wizard-prompt-text"


def _open_projects(page) -> None:
    page.evaluate(
        "() => window.AssureNav && window.AssureNav.switchView('projects', {replaceHash: false, persist: false})"
    )
    page.wait_for_selector("#view-projects", state="visible")


def test_wizard_creates_project(page, base_url):
    from tests.playwright.helpers import prime_page

    prime_page(page)
    page.goto(f"{base_url.rstrip('/')}/app", wait_until="domcontentloaded")
    _open_projects(page)
    page.wait_for_selector("#projects-new-btn", state="visible")
    page.locator("#projects-new-btn").click()
    page.wait_for_selector(WIZARD, state="visible")
    page.wait_for_selector('[data-template-id="blank"]', state="visible")

    page.locator('[data-template-id="blank"]').click()
    page.locator(WIZARD_NEXT).click()
    page.wait_for_selector("#wizard-step-sources", state="visible")
    page.locator(WIZARD_NEXT).click()
    page.wait_for_selector("#wizard-step-prompt", state="visible")

    title = f"PW Wizard {int(time.time())}"
    page.locator(WIZARD_TITLE).fill(title)
    page.locator(WIZARD_PROMPT).fill("Summarize compliance risks in plain language.")

    page.evaluate(
        "() => { if (window.AssureUnsaved && window.AssureUnsaved.clearUnsaved) window.AssureUnsaved.clearUnsaved(); }"
    )

    with page.expect_response(
        lambda r: "/api/projects" in r.url and r.request.method == "POST", timeout=30_000
    ) as resp_info:
        page.locator(WIZARD_CREATE).click()
    response = resp_info.value
    assert response.status == 201
    body = response.json()
    assert body.get("ok") is True
    project_id = body["id"]

    page.wait_for_selector(WIZARD, state="hidden", timeout=15_000)
    page.wait_for_function(
        "(pid) => window.__ASSURE_PROJECT_ID__ === pid",
        arg=project_id,
        timeout=15_000,
    )

    intent = page.locator("#generate-intent")
    assert "compliance" in (intent.input_value() or "").lower()
