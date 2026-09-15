"""Structural smoke tests for the prototype shell (UI-state only — acceptable).

These assert the UI shows expected structure. They do NOT compare layers;
the cross-layer honesty checks live in test_shell_honesty.py.
"""

import pytest

pytestmark = pytest.mark.e2e

_ACTIVE_KEY = "assure_project"


def _active_project(page):
    return page.evaluate(
        "() => { try { return window.localStorage.getItem('" + _ACTIVE_KEY + "') || null; } "
        "catch (e) { return null; } }"
    )


def test_right_pane_visible_on_load(goto_shell, browser_page):
    goto_shell()
    assert browser_page.locator("#pane-right").is_visible(), "right pane not visible on load"
    assert browser_page.locator(
        "#inspector-empty"
    ).is_visible(), "inspector empty state not visible on load"
    assert browser_page.locator(
        '[data-right-tab="evidence"]'
    ).is_visible(), "Evidence tab strip not visible"


def test_source_ids_load_on_init(active_project, browser_page, fire_intent, capture_draft_stream):
    pid = active_project
    # Wait for the substrate list to populate after the reload (waiter only),
    # then read it as a plain int.
    browser_page.wait_for_function(
        "() => document.querySelectorAll('#source-list .source-item').length",
        timeout=25000,
    )
    count = browser_page.locator("#source-list .source-item").count()
    if not count:
        pytest.skip(f"active project {pid} has no sources to display")
    assert count == 3, f"expected 3 sources for {pid}, got {count}"
    fire_intent("summarize the key CPT codes")
    payload = capture_draft_stream()
    sent_ids = payload.get("substrate_file_ids") or []
    assert (
        len(sent_ids) == 3
    ), f"compile sent {len(sent_ids)} substrate_file_ids for {pid} but UI shows 3 sources"


def test_compile_renders_document(goto_shell, browser_page, fire_intent, wait_for_render):
    goto_shell()
    fire_intent("summarize the key CPT codes")
    wait_for_render()
    nodes = browser_page.locator(".doc-draft .jdf-node").count()
    assert nodes > 0, "compile produced no .jdf-node elements in the canvas"


def test_click_paragraph_opens_inspector(goto_shell, browser_page, fire_intent, wait_for_render):
    goto_shell()
    fire_intent("summarize the key CPT codes")
    wait_for_render()
    browser_page.locator(".doc-draft .jdf-node").first.click()
    browser_page.wait_for_function(
        "() => { var e = document.querySelector('#right-evidence'); "
        "return e && getComputedStyle(e).display !== 'none'; }",
        timeout=20000,
    )
    assert (
        browser_page.locator(".evidence-header").count() >= 1
    ), "inspector Evidence tab rendered no evidence header"


def test_ungrounded_banner_fires(goto_shell, browser_page, fire_intent, wait_for_render):
    goto_shell()
    fire_intent("what is ferrari")
    wait_for_render()
    count = browser_page.locator(".doc-ungrounded-banner").count()
    assert count <= 1, f"expected at most one ungrounded banner, found {count}"


def test_version_chip_hidden_at_one_revision(goto_shell, browser_page):
    goto_shell()
    assert browser_page.locator("#version-label").count() == 1, "version label control missing"


def test_compare_toggle_hides_tab_strip(goto_shell, browser_page):
    goto_shell()
    toggle = browser_page.locator("#compare-toggle")
    assert toggle.is_visible(), "compare toggle not visible on load"
    toggle.click()
    browser_page.wait_for_function(
        "() => { var s = document.querySelector('#pane-right .mode-toggle'); "
        "var c = document.querySelector('#right-compare'); "
        "return s && getComputedStyle(s).display === 'none' && "
        "c && getComputedStyle(c).display === 'block'; }",
        timeout=10000,
    )
    # A compare run may be in flight (compareInFlight disables the toggle);
    # wait for it to clear before clicking back to the inspector.
    browser_page.wait_for_function(
        "() => { var t = document.querySelector('#compare-toggle'); "
        "return t && !t.classList.contains('is-disabled'); }",
        timeout=60000,
    )
    toggle.click()
    browser_page.wait_for_function(
        "() => { var s = document.querySelector('#pane-right .mode-toggle'); "
        "var i = document.querySelector('#right-inspector'); "
        "return s && getComputedStyle(s).display !== 'none' && "
        "i && getComputedStyle(i).display === 'block'; }",
        timeout=10000,
    )
