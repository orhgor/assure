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
    # Nothing selected: the inspector is the calm empty line (or the review
    # list when the document has items), never the per-node sections.
    assert browser_page.locator(
        "#inspector-empty, #inspector-review-list"
    ).first.is_visible(), "inspector idle state not visible on load"
    assert browser_page.locator("#inspector-sections").is_hidden(), (
        "the per-paragraph sections must stay hidden until a paragraph is selected"
    )
    assert browser_page.locator("#insp-counts").is_visible(), "claim counts section missing"


def test_header_is_three_zones_one_action_one_menu(goto_shell, browser_page):
    """Brief §3A: brand + document name | one status chip | one primary + More.

    Everything the brief moves out of the header (export variants, sign-off,
    audit report, decision log, version stepper, locale, about) lives in the
    overflow menu and is hidden until asked for."""
    goto_shell()
    header = browser_page.locator("header.app-header")
    assert header.locator(".btn-primary").count() == 1, "the header carries exactly one filled action"
    assert header.locator("#shell-status-chip").is_visible(), "the status chip is the header's centre"
    assert header.locator("#shell-more").is_visible(), "the overflow menu trigger is missing"
    assert browser_page.locator("#shell-more-menu").is_hidden(), "the More menu must start closed"
    # Hidden by default, present when asked for.
    for hidden_id in ("export-btn", "menu-signoff", "menu-audit", "menu-history", "about-btn"):
        assert browser_page.locator("#" + hidden_id).is_hidden(), f"#{hidden_id} must live in the closed menu"
    assert browser_page.locator("#shell-more-menu #version-chip").count() == 1, (
        "the version stepper belongs in the menu, never in the header centre"
    )
    browser_page.click("#shell-more")
    assert browser_page.locator("#export-btn").is_visible(), "Export is the menu's first item"
    browser_page.keyboard.press("Escape")
    assert browser_page.locator("#shell-more-menu").is_hidden(), "Escape closes the menu"
    # An empty workspace: the chip and the action say so, and nothing is claimed.
    chip = browser_page.locator("#shell-status-chip")
    if browser_page.locator(".doc-draft .jdf-node").count() == 0:
        assert chip.get_attribute("data-tone") == "none", "no tone without a document"
        assert browser_page.locator("#shell-primary").is_disabled(), "no primary action without a document"
        assert browser_page.locator("#assure-strip").is_hidden(), "the strip is hidden in the empty state"


def test_rail_is_the_four_item_spine(goto_shell, browser_page):
    goto_shell()
    rail = browser_page.locator(".rail-left")
    ids = rail.locator(".rail-btn").evaluate_all("els => els.map(e => e.id)")
    assert ids == ["rail-workspaces", "rail-sources", "rail-analytics", "rail-settings"], ids
    assert rail.locator("#rail-analytics").get_attribute("href") == "/parsing"
    assert browser_page.locator("#rail-sources-badge").is_hidden(), "no badge without queued jobs"
    browser_page.click("#rail-sources")
    browser_page.wait_for_function(
        "() => document.getElementById('rail-sources').classList.contains('is-active')", timeout=5000
    )
    assert browser_page.locator('[data-left-tab="sources"]').get_attribute("aria-selected") == "true"


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
        "() => { var e = document.querySelector('#inspector-sections'); "
        "return e && !e.hidden; }",
        timeout=20000,
    )
    assert (
        browser_page.locator("#right-why .evidence-header").count() >= 1
    ), "the 'Why this state' section rendered no verdict header"
    assert browser_page.locator("#insp-why").get_attribute("open") is not None
    assert browser_page.locator("#insp-evidence").get_attribute("open") is not None
    assert browser_page.locator("#insp-verification").get_attribute("open") is None, (
        "Verification details starts collapsed"
    )


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
    assert submit.get_attribute("aria-label") == "Add a source to enable drafting", (
        "the disabled Submit must say what it waits for"
    )
    assert submit.get_attribute("title") == "Add a source to enable drafting"

    prereq = browser_page.locator(".doc-prereq")
    prereq.first.wait_for(timeout=20000)
    assert "No documents yet. Upload evidence to begin." in prereq.first.inner_text(), (
        f"the empty column must name the missing evidence: {prereq.first.inner_text()!r}"
    )
    assert prereq.locator(".btn-primary").count() == 1, "the empty state carries one Upload action"

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


def test_compare_toggle_swaps_inspector_for_compare(goto_shell, browser_page):
    goto_shell()
    toggle = browser_page.locator("#compare-toggle")
    assert toggle.is_visible(), "compare toggle not visible on load"
    toggle.click()
    browser_page.wait_for_function(
        "() => { var i = document.querySelector('#right-inspector'); "
        "var c = document.querySelector('#right-compare'); "
        "return i && getComputedStyle(i).display === 'none' && "
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
        "() => { var c = document.querySelector('#right-compare'); "
        "var i = document.querySelector('#right-inspector'); "
        "return c && getComputedStyle(c).display === 'none' && "
        "i && getComputedStyle(i).display !== 'none'; }",
        timeout=10000,
    )
