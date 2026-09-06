"""First-compile coachmark on new projects."""

from __future__ import annotations

import pytest

from tests.playwright.helpers import COMPILE_BTN, route_draft_success, wait_compile_ready

pytestmark = pytest.mark.playwright

COACHMARK = "#first-compile-coachmark"


def test_coachmark_shows_and_dismisses_on_compile(page, base_url):
    from tests.playwright.helpers import prime_page

    prime_page(page)
    page.goto(f"{base_url.rstrip('/')}/app", wait_until="domcontentloaded")

    created = page.evaluate(
        """async () => {
          const res = await fetch('/api/projects', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              title: 'Coachmark PW',
              template_id: 'blank',
              prompt: 'Write a short intro paragraph.',
            }),
          });
          return res.json();
        }"""
    )
    assert created.get("ok"), created

    pid = created["id"]
    page.evaluate(
        """(id) => {
          if (window.AssureProjects && window.AssureProjects.switchTo) {
            window.AssureProjects.switchTo(id, 'Coachmark PW', { skipUnsaved: true });
          }
          if (window.AssureNav) {
            window.AssureNav.switchView('generate', { replaceHash: false, persist: false });
          }
        }""",
        pid,
    )
    page.wait_for_selector(COMPILE_BTN, state="visible")
    page.evaluate(
        "() => window.AssureFirstCompileCoachmark && window.AssureFirstCompileCoachmark.check(true)"
    )
    page.wait_for_selector(COACHMARK, state="visible")

    page.route("**/draft/stream", route_draft_success)
    page.locator("#generate-intent").fill("One paragraph about team safety.")
    page.locator(COMPILE_BTN).click()
    wait_compile_ready(page)
    assert page.locator(COACHMARK).is_hidden()
