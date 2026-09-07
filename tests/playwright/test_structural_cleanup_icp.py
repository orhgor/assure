"""Structural cleanup + Big Four ICP templates."""

from __future__ import annotations

import time

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.playwright


def test_header_scoping(workbench_page: Page):
    page = workbench_page
    page.evaluate(
        "() => window.AssureNav.switchView('projects', {replaceHash:false, persist:false})"
    )
    expect(page.locator("#panel-write")).to_be_visible()
    expect(page.locator("#document-chrome")).to_be_hidden()
    expect(page.locator("#export-menu")).to_be_hidden()
    expect(page.locator("#btn-audit-manifest")).to_be_hidden()
    expect(page.locator("#compliance-lock-btn")).to_be_hidden()
    expect(page.locator("#header-command-palette-btn")).to_be_visible()
    expect(page.locator("#engine-status")).to_be_visible()
    expect(page.locator("#locale-select")).to_be_visible()
    expect(page.locator("#header-user-avatar")).to_be_visible()


def test_canvas_state_sync(workbench_page: Page):
    page = workbench_page
    page.evaluate(
        "() => window.AssureNav.switchView('projects', {replaceHash:false, persist:false})"
    )
    expect(page.locator("#workspace-canvas-none")).to_be_visible()
    expect(page.locator(".project-active-badge")).to_have_count(0)
    page.locator(".project-work-open").first.click()
    expect(page.locator(".project-work-card.is-active")).to_have_count(1)
    expect(page.locator(".project-active-badge")).to_have_count(1)
    expect(page.locator("#workspace-canvas-selected")).to_be_visible()
    expect(page.locator("#workspace-open-compiler")).to_be_visible()
    expect(page.locator("#canvas-stage")).to_be_hidden()


def test_workspace_delete(workbench_page: Page):
    page = workbench_page
    title = f"Delete target {int(time.time())}"
    created = page.evaluate(
        """async (title) => {
          const res = await fetch('/api/projects', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title }),
          });
          return res.json();
        }""",
        title,
    )
    assert created.get("ok")
    page.evaluate(
        "() => window.AssureNav.switchView('projects', {replaceHash:false, persist:false})"
    )
    card = page.locator(f'.project-work-card[data-project-id="{created["id"]}"]')
    expect(card).to_be_visible()
    card.locator(".project-overflow-toggle").click()
    card.locator('[data-tool="delete"]').click()
    expect(page.locator("#workspace-confirm-dialog")).to_be_visible()
    page.locator("#workspace-confirm-ok").click()
    expect(card).to_have_count(0)


def test_lazy_creation(workbench_page: Page):
    page = workbench_page
    posts = []

    def on_request(request):
        if (
            request.method == "POST"
            and "/api/projects" in request.url
            and request.url.rstrip("/").endswith("/projects")
        ):
            posts.append(request.url)

    page.on("request", on_request)
    page.evaluate(
        "() => window.AssureNav.switchView('projects', {replaceHash:false, persist:false})"
    )
    page.locator("#projects-new-btn").click()
    expect(page.locator("#workspace-canvas-create")).to_be_visible()
    expect(page.locator('[data-create-template="research-dossier"]')).to_be_visible()
    page.wait_for_timeout(400)
    assert posts == []


def test_wizard_templates(workbench_page: Page):
    page = workbench_page
    page.evaluate(
        """async () => {
          await window.AssureNewProjectWizard.open('research-dossier');
        }"""
    )
    expect(page.locator("#new-project-wizard")).to_be_visible()
    cards = page.locator("#wizard-template-grid .wizard-template-card")
    expect(cards).to_have_count(4)
    expect(cards.filter(has_text="Research Dossier")).to_have_count(1)
    expect(cards.filter(has_text="Compliance Memo")).to_have_count(1)
    expect(cards.filter(has_text="Contract Review")).to_have_count(1)
    expect(cards.filter(has_text="Blank Workspace")).to_have_count(1)
    expect(cards.filter(has_text="Synthesize scattered findings")).to_have_count(1)
    expect(cards.filter(has_text="Audit regulatory filings")).to_have_count(1)
