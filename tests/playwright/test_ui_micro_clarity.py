"""Enterprise micro-clarity — copy, stepper isolation, workspaces a11y."""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from tests.playwright.helpers import (
    COMPILE_BTN,
    COMPILE_INPUT,
    FULL_AUDIT_BTN,
    app_url,
    enter_compiler,
    goto_workbench,
    prime_page,
)


def _open_workspaces(page: Page) -> None:
    page.evaluate(
        """() => {
          if (window.AssureNav) window.AssureNav.switchView('projects', {replaceHash: true});
        }"""
    )
    page.wait_for_selector("#panel-write:not([hidden])", timeout=15_000)


def test_stepper_stage_isolation(page: Page, base_url: str) -> None:
    prime_page(page)
    goto_workbench(page, base_url)

    expect(page.locator(COMPILE_INPUT)).to_be_visible()
    expect(page.locator(COMPILE_BTN)).to_be_visible()
    expect(page.locator(FULL_AUDIT_BTN)).to_be_hidden()

    page.evaluate(
        """() => {
          if (window.AssureStepper) window.AssureStepper.setPhase('verify', 'active');
        }"""
    )
    expect(page.locator(COMPILE_INPUT)).to_be_hidden()
    expect(page.locator(FULL_AUDIT_BTN)).to_be_visible()
    expect(page.locator(COMPILE_BTN)).to_be_hidden()

    page.evaluate(
        """() => {
          if (window.AssureStepper) window.AssureStepper.setPhase('audit', 'active');
        }"""
    )
    expect(page.locator("#generate-redhat-btn")).to_be_visible()
    expect(page.locator(FULL_AUDIT_BTN)).to_be_hidden()

    page.evaluate(
        """() => {
          if (window.AssureStepper) window.AssureStepper.setPhase('ship', 'active');
        }"""
    )
    expect(page.locator("#generate-accept-dock-phase")).to_be_visible()
    expect(page.locator("#generate-redhat-btn")).to_be_hidden()


def test_enterprise_copy(page: Page, base_url: str) -> None:
    prime_page(page)
    goto_workbench(page, base_url)

    expect(page.locator('[data-i18n="generate.draft_header"]')).to_contain_text(
        "Draft or Import Document"
    )
    expect(page.locator("#argument-spine-empty")).to_contain_text(
        "Section hierarchy and argument spine generate upon document assembly."
    )
    expect(page.locator("#refine-workspace-empty")).to_contain_text(
        "Select a compiled paragraph to refine language and structure."
    )
    expect(page.locator('[data-role-widget="risk"] .panel-placeholder')).to_contain_text(
        "No audit executed. Risk signals and contradictions populate after Full Audit."
    )


def test_accessibility_action_menu(page: Page, base_url: str) -> None:
    prime_page(page)
    page.goto(app_url(base_url), wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_selector("#workbench-root", state="visible", timeout=30_000)
    _open_workspaces(page)

    toggle = page.locator(".project-overflow-toggle").first
    expect(toggle).to_have_attribute("aria-label", "Workspace actions menu")
    expect(toggle).to_have_attribute("title", "Workspace actions")


def test_context_aware_empty_states(page: Page, base_url: str) -> None:
    prime_page(page)
    page.goto(app_url(base_url), wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_selector("#workbench-root", state="visible", timeout=30_000)

    page.route(
        "**/api/projects",
        lambda route: route.fulfill(status=200, json={"ok": True, "projects": []}),
    )
    _open_workspaces(page)
    page.evaluate("""() => { if (window.AssureProjects) window.AssureProjects.load(); }""")
    expect(page.locator(".projects-dashboard-empty")).to_contain_text(
        "No workspaces yet. Click + New to initialize your first project."
    )

    page.route(
        "**/api/projects",
        lambda route: route.fulfill(
            status=200,
            json={
                "ok": True,
                "projects": [
                    {
                        "id": "prj_only_audited",
                        "title": "Audited One",
                        "status": "audited",
                        "updated_at": "2026-09-01 12:00:00",
                    }
                ],
            },
        ),
    )
    page.evaluate("""() => { if (window.AssureProjects) window.AssureProjects.load(); }""")
    page.locator('#projects-status-tabs [data-filter="drafting"]').click()
    expect(page.locator(".projects-dashboard-empty")).to_contain_text(
        "No Drafting workspaces found."
    )
    page.locator('#projects-status-tabs [data-filter="audited"]').click()
    expect(page.locator(".project-work-card")).to_have_count(1)
    page.locator('#projects-status-tabs [data-filter="archived"]').click()
    expect(page.locator(".projects-dashboard-empty")).to_contain_text(
        "No Archived workspaces found."
    )


def test_character_counter(page: Page, base_url: str) -> None:
    prime_page(page)
    goto_workbench(page, base_url)

    counter = page.locator("#generate-intent-counter")
    page.wait_for_function(
        """() => {
          const el = document.getElementById('generate-intent-counter');
          return el && el.textContent.includes('0 words');
        }"""
    )
    expect(counter).to_contain_text("0 words")

    page.locator(COMPILE_INPUT).fill("Hello enterprise draft")
    page.wait_for_function(
        """() => {
          const el = document.getElementById('generate-intent-counter');
          return el && el.textContent.includes('3 words');
        }"""
    )
    counter = page.locator("#generate-intent-counter")
    expect(counter).to_contain_text("3 words")
    expect(counter).to_contain_text("characters")


def test_archived_visual(page: Page, base_url: str) -> None:
    prime_page(page)
    page.goto(app_url(base_url), wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_selector("#workbench-root", state="visible", timeout=30_000)

    page.route(
        "**/api/projects",
        lambda route: route.fulfill(
            status=200,
            json={
                "ok": True,
                "projects": [
                    {
                        "id": "prj_archived_demo",
                        "title": "prj_archived_demo",
                        "status": "drafting",
                        "updated_at": "2026-09-01 12:00:00",
                    }
                ],
            },
        ),
    )
    _open_workspaces(page)
    page.evaluate(
        """() => {
          localStorage.setItem('assure_archived_workspaces', JSON.stringify(['prj_archived_demo']));
          if (window.AssureProjects) window.AssureProjects.load();
        }"""
    )
    page.locator('#projects-status-tabs [data-filter="archived"]').click()
    card = page.locator(".project-work-card.is-archived")
    expect(card).to_have_count(1)
    expect(card.locator(".project-archived-badge")).to_contain_text("Archived")
    border = card.evaluate("el => getComputedStyle(el).borderColor")
    assert border in ("rgb(209, 213, 219)", "rgb(209, 213, 219)")
