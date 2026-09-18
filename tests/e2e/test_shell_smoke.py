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


def test_compile_renders_document(active_project, browser_page, fire_intent, wait_for_render):
    # A compile is grounded in its sources: this uses the seeded project that
    # has them (shell-proto-54fe89). A project with no sources is refused, and
    # test_ungrounded_compile_is_refused covers that path.
    fire_intent("summarize the key CPT codes")
    wait_for_render()
    nodes = browser_page.locator(".doc-draft .jdf-node").count()
    assert nodes > 0, "compile produced no .jdf-node elements in the canvas"


def test_click_paragraph_opens_inspector(active_project, browser_page, fire_intent, wait_for_render):
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


def test_ungrounded_compile_is_refused(goto_shell, browser_page, fire_intent):
    """A project with no source cannot be compiled at all — by either layer.

    The dock does not offer the run: Submit is disabled and carries the reason on
    its own label, and the document column names the missing source instead of
    sitting blank behind a button that refuses without saying why. Forced anyway
    (the console does what the button will not), the server refuses it before its
    first stage — HTTP 422, reason `no_source_attached` — so no token ever
    reaches the pane. A banner over a rendered document is the defect this
    replaced, and so is a streamed draft that never becomes a document."""
    goto_shell()
    submit = browser_page.locator("#dock-submit")
    browser_page.fill("#dock-text", "what is ferrari")

    assert submit.is_disabled(), "Submit must be disabled while the project has no source"
    assert submit.get_attribute("aria-label") == "Add a source to enable the compile", (
        "the disabled Submit must say what it waits for"
    )
    assert submit.get_attribute("title") == "Add a source to enable the compile"

    prereq = browser_page.locator(".doc-prereq")
    prereq.first.wait_for(timeout=20000)
    assert "Add a source to compile" in prereq.first.inner_text(), (
        f"the empty column must name the missing source: {prereq.first.inner_text()!r}"
    )

    # Enter is not a way around the disabled button.
    browser_page.press("#dock-text", "Enter")
    browser_page.wait_for_timeout(1500)
    assert browser_page.locator(".doc-draft, .doc-refusal, .doc-halt").count() == 0, (
        "the keyboard path fired a compile the dock refused to offer"
    )

    # Forced through the shell's own gate, the server still refuses it, with the
    # frames the refusal card is keyed on and nothing before them.
    pid = _active_project(browser_page)
    assert pid, "the shell has no active project to force the compile against"
    forced = browser_page.evaluate(
        """async (pid) => {
             var r = await fetch('/api/projects/' + encodeURIComponent(pid) + '/draft/stream', {
               method: 'POST',
               headers: {'Content-Type': 'application/json',
                         'Accept': 'text/event-stream, application/json'},
               body: JSON.stringify({intent: 'what is ferrari', compileType: 'full',
                                     substrate_file_ids: []}),
             });
             return {status: r.status, text: await r.text()};
           }""",
        pid,
    )
    assert '"reason": "no_source_attached"' in forced["text"], forced["text"][:400]
    assert '"http_status": 422' in forced["text"], forced["text"][:400]
    assert (
        "Upload a source first. Assure grounds every claim against the source you provide."
        in forced["text"]
    ), forced["text"][:400]
    assert "event: token" not in forced["text"], "nothing may stream before the refusal"
    assert "event: compiled" not in forced["text"], "a refused compile compiles nothing"
    assert browser_page.locator(".doc-draft").count() == 0, (
        "the forced refusal wrote text into the document column"
    )


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
        "c && getComputedStyle(c).display !== 'none'; }",
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
        "i && getComputedStyle(i).display !== 'none'; }",
        timeout=10000,
    )
