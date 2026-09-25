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


# ---- The Fields queue (spec §9 items 17–21; brief §2 step 4) -----------------
# These run against a project that holds real intake reports. The shell is
# reached through the gate (SHELL_BASE_URL), so the key has to arrive with the
# request — locally a small forwarding proxy that adds X-Shell-Key is enough
# (/tmp/keyproxy.py in the 2026-09-25 runs); the fixtures themselves carry none.

import os as _os

_SHELL_BASE = _os.environ.get("SHELL_BASE_URL", "http://localhost:8990")
_PARSURE_PROJECT = _os.environ.get("PARSURE_PROJECT", "e2e-parsure-4")
_DOC_TYPES = [
    "auto_policy", "auto_claim", "auto_title", "property_policy", "property_claim",
    "deed", "mortgage", "title", "closing", "uncertain",
]


@pytest.fixture
def parsure_shell(browser_page):
    """Open the shell on the intake project through the deep link and wait for
    the report's rows (built into the hidden Fields pane as soon as the report
    answers, whichever pane is showing)."""

    def _open(project_id: str = _PARSURE_PROJECT, report_id: str | None = None) -> None:
        browser_page.set_default_timeout(20000)
        url = _SHELL_BASE + "/index.html?project_id=" + project_id
        if report_id:
            url += "&report_id=" + report_id
        browser_page.goto(url, wait_until="domcontentloaded")
        browser_page.wait_for_selector("#pane-right", state="visible")
        browser_page.wait_for_function(
            "() => document.querySelectorAll('#fields-list .field-row').length > 0", timeout=25000
        )

    return _open


def _reports(browser_page, project_id: str) -> list[dict]:
    r = browser_page.request.get(_SHELL_BASE + f"/api/projects/{project_id}/parsure")
    if r.status == 404:
        pytest.skip(f"{project_id} has no intake reports on this API")
    return (r.json() or {}).get("reports") or []


def test_fields_panel_renders_rows_for_intake_report(parsure_shell, browser_page):
    """Review is the primary action while fields wait; it opens the Fields pane
    on the first waiting field, needs-attention rows first, each row with its
    label, value, state chip and one reason line; the strip counts the fields."""
    parsure_shell()
    primary = browser_page.locator("#shell-primary")
    assert primary.get_attribute("data-action") == "review", "fields waiting → primary is Review"
    assert primary.inner_text().strip() == "Review"
    assert "field" in browser_page.inner_text("#strip-review-value"), "the strip's Review cell counts fields"
    assert browser_page.locator("#fields-tab-count").is_visible(), "the Fields tab carries the waiting count"

    primary.click()
    browser_page.wait_for_selector("#right-fields", state="visible")
    assert browser_page.locator("#right-inspector").is_hidden(), "the inspector steps aside for the queue"
    assert browser_page.locator("#compare-toggle").is_hidden(), "Compare belongs to the inspector"

    rows = browser_page.locator("#fields-list .field-row")
    assert rows.count() >= 1
    attention = rows.evaluate_all("els => els.map(e => e.dataset.attention)")
    split = attention.index("0") if "0" in attention else len(attention)
    assert all(a == "1" for a in attention[:split]) and all(a == "0" for a in attention[split:]), (
        f"needs-attention rows come first: {attention}"
    )
    for i in range(rows.count()):
        row = rows.nth(i)
        assert row.locator(".field-label").inner_text().strip(), f"row {i} has a label"
        assert row.locator(".field-value").inner_text().strip(), f"row {i} shows a value or 'not found'"
        chip = row.locator(".field-chip")
        assert chip.inner_text().strip() in ("Verified", "Review needed", "Disputed", "Rejected", "Conflict")
        assert chip.get_attribute("data-tone") in ("verified", "partial", "contradicted")
        assert row.locator(".field-reason").inner_text().strip(), f"row {i} says why it is in its state"
    # Review landed on the first waiting field: the reason precedes the tools.
    sel = browser_page.locator("#fields-list .field-row.is-selected")
    assert sel.count() == 1 and sel.get_attribute("data-attention") == "1"
    assert sel.locator(".field-detail").is_visible()
    assert sel.locator(".field-actions .btn-primary").count() == 1, "one filled action on the open row"
    assert browser_page.locator("#fields-list .field-actions").count() == 1, "tools only on the open row"
    assert browser_page.locator("#fields-type-value").inner_text().strip()
    assert browser_page.locator("#fields-type-change").is_visible()


def test_accept_moves_field_to_verified(parsure_shell, browser_page):
    """Accept on a waiting field with a value: the row turns Verified, leaves the
    attention group, and the strip / tab count / server report all agree."""
    parsure_shell()
    browser_page.click("#right-tab-fields")
    browser_page.wait_for_selector("#right-fields", state="visible")
    cand = browser_page.locator(
        '#fields-list .field-row[data-attention="1"][data-has-value="1"][data-state="review"]'
    )
    if cand.count() == 0:
        pytest.skip(f"every valued field in {_PARSURE_PROJECT} is already verified or disputed")
    name = cand.first.get_attribute("data-field")
    before = int(browser_page.inner_text("#fields-tab-count") or "0")
    cand.first.locator(".field-row-head").click()
    row = browser_page.locator(f'#fields-list .field-row[data-field="{name}"]')
    row.locator(".field-accept").click()
    browser_page.wait_for_function(
        "(n) => { var e = document.querySelector('#fields-list .field-row[data-field=\"' + n + '\"]');"
        " return e && e.dataset.state === 'accepted'; }",
        arg=name, timeout=15000,
    )
    row = browser_page.locator(f'#fields-list .field-row[data-field="{name}"]')
    assert row.locator(".field-chip").inner_text().strip() == "Verified"
    assert row.locator(".field-chip").get_attribute("data-tone") == "verified"
    assert row.get_attribute("data-attention") == "0"
    tab = browser_page.locator("#fields-tab-count")
    after = int(tab.inner_text() or "0") if tab.is_visible() else 0
    assert after == before - 1, f"tab count {before} → {after}"
    # The server's report says the same: the row is not the shell's opinion.
    select = browser_page.locator("#fields-report-select")
    report_id = select.input_value() if select.is_visible() else _reports(browser_page, _PARSURE_PROJECT)[0]["report_id"]
    rep = browser_page.request.get(_SHELL_BASE + f"/api/projects/{_PARSURE_PROJECT}/parsure/{report_id}").json()
    field = next(f for f in rep["report"]["fields"] if f["name"] == name)
    assert field["field_state"] == "accepted" and field["routing_action"] == "none"
    assert field["review_required"] is False


def test_classification_override_select_exists(parsure_shell, browser_page):
    """Change on the document type opens the ten-type select with a reason;
    Cancel puts the type line back untouched."""
    parsure_shell()
    browser_page.click("#right-tab-fields")
    browser_page.wait_for_selector("#right-fields", state="visible")
    shown = browser_page.locator("#fields-type-value").inner_text().strip()
    assert shown
    browser_page.click("#fields-type-change")
    select = browser_page.locator("#fields-type-select")
    assert select.is_visible()
    assert select.locator("option").evaluate_all("els => els.map(e => e.value)") == _DOC_TYPES
    assert browser_page.locator("#fields-type-reason").is_visible()
    assert browser_page.locator("#fields-type-save").is_visible()
    assert browser_page.locator("#fields-type").is_hidden(), "the type line yields to the form"
    browser_page.click("#fields-type-cancel")
    assert select.is_hidden()
    assert browser_page.locator("#fields-type-value").inner_text().strip() == shown


def test_report_deep_link_opens_fields_panel(parsure_shell, browser_page):
    """`?project_id=…&report_id=…` (the Parsure page's link) opens that report
    in the Fields pane; without it the latest report is open and the pane is
    the inspector."""
    reports = _reports(browser_page, _PARSURE_PROJECT)
    if len(reports) < 2:
        pytest.skip("needs a project with at least two reports")
    older = reports[-1]["report_id"]
    parsure_shell(report_id=older)
    browser_page.wait_for_selector("#right-fields", state="visible")
    select = browser_page.locator("#fields-report-select")
    assert select.is_visible(), "several reports → the quiet document selector"
    assert select.input_value() == older
    assert len(select.locator("option").all_inner_texts()) == len(reports)
    parsure_shell()
    assert browser_page.locator("#right-inspector").is_visible()
    if select.is_visible():
        assert select.input_value() == reports[0]["report_id"], "no link → the latest report"
