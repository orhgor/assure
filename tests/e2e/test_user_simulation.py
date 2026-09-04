"""Playwright crash / resilience simulation for the Assure JDF workbench.

Exercises client-side draft persistence, stream lock (beforeunload), compile +
Red-Hat refine loop, SSE reconnect/error handling, mobile lockout, second-tab
freeze, and the session compile cap.

SSE bodies are mocked via ``page.route`` on ``POST /api/projects/.../draft/stream``
(and inquire/stream). There is no ``POST /compile``. Tests run without live
model API keys and without Clerk (Flask ``require_auth=False``).

Run locally:
  uv sync --extra dev
  uv run playwright install chromium   # skip if ENOSPC; conftest uses system Chrome
  uv run pytest tests/e2e/test_user_simulation.py -v --tb=short

Optional: ASSURE_BASE_URL=http://127.0.0.1:8765 pytest ...  (skip embedded server)
"""

from __future__ import annotations

import json
import os
import re
import time

import pytest

pytestmark = pytest.mark.playwright

# ---------------------------------------------------------------------------
# Selectors / storage keys aligned with templates/index.html + static JS
# ---------------------------------------------------------------------------

DRAFT_STORAGE_KEY = "assure_draft_prompt"
ONBOARDING_KEY = "assure_onboarding_complete"
SESSION_COMPILE_KEY = "assure_session_compiles"
SESSION_COMPILE_LIMIT = 15
# assure_unsaved.js DEBOUNCE_MS = 1000; wait past that before reload.
DRAFT_DEBOUNCE_WAIT_MS = 1200
TAB_GUARD_ESTABLISH_MS = 400  # assure_tab_guard.js PING_TIMEOUT_MS
DESKTOP_VIEWPORT = {"width": 1440, "height": 900}
MOBILE_VIEWPORT = {"width": 390, "height": 844}

COMPILE_INPUT = "#generate-intent"
COMPILE_BTN = "#generate-compile-btn"
SAVE_PILL = "#save-status"
ACCEPT_DOCK_BTN = "#generate-accept-dock"
INQUIRY_INPUT = "#inquiry-input"
INQUIRE_BTN = "#btn-inquire"
REDHAT_TOGGLE = "#toggle-redhat"
CANVAS_NODE = "#jdf-render-target .jdf-node"
TOAST = ".toast-root .toast"
DIFF_ACCEPT = ".diff-accept"
MOBILE_LOCKOUT = "#mobile-lockout"
TAB_LOCKOUT = "#tab-lockout-modal"
SESSION_BADGE = "#session-compile-limit"
WORKBENCH = "#jdf-workbench"
ASSURE_APP = "#assure-app"
APP_LOGO = ".app-logo"
RECONNECT_TOAST = "Connection dropped — retrying…"
SESSION_LIMIT_LABEL = "Session Limit Reached"

COMPLEX_PROMPT = (
    "Q3 investor update: revenue $12M ARR (+45% YoY), runway 18 months, "
    "include Red-Hat audit on growth claims vs industry benchmark."
)

NODE_ID = "para-sim-1"
SECTION_ID = "sec-sim-1"


def _app_url(base_url: str) -> str:
    env_base = os.environ.get("ASSURE_BASE_URL")
    root = (env_base or base_url).rstrip("/")
    return f"{root}/app"


def _init_script(*, compiles: int | None = 0) -> str:
    """Skip the first-visit tour and control the session compile counter.

    Onboarding historically trapped testers (Next off-screen). Completing it
    via localStorage matches ``onboarding.js`` (``assure_onboarding_complete``).
    ``compiles=None`` leaves sessionStorage alone (used when a later init
    script sets the cap).
    """
    compile_js = ""
    if compiles is not None:
        compile_js = f"sessionStorage.setItem({SESSION_COMPILE_KEY!r}, '{int(compiles)}');"
    return f"""
    try {{
      localStorage.setItem({ONBOARDING_KEY!r}, '1');
      {compile_js}
    }} catch (e) {{}}
    """


def _prime(page, *, compiles: int | None = 0, viewport: dict | None = None) -> None:
    page.set_viewport_size(viewport or DESKTOP_VIEWPORT)
    page.add_init_script(_init_script(compiles=compiles))


def _goto_workbench(page, base_url: str, *, wait_canvas: bool = True):
    page.goto(_app_url(base_url), wait_until="domcontentloaded")
    if wait_canvas:
        page.wait_for_selector(WORKBENCH, state="visible")
        page.wait_for_function(
            "() => window.__assureJdf && typeof window.__assureJdf.render === 'function'"
        )
        page.wait_for_function("() => !document.body.classList.contains('onboarding-active')")
        page.evaluate(
            "() => window.AssureNav && window.AssureNav.switchView('generate', {replaceHash: false, persist: false})"
        )
        page.wait_for_selector(COMPILE_BTN, state="visible")
    return page


def _fill_and_compile(page, prompt: str) -> None:
    """Type intent and click Compile (POST /api/projects/.../draft/stream)."""
    page.locator(COMPILE_INPUT).fill(prompt)
    page.locator(COMPILE_BTN).click()


# ---------------------------------------------------------------------------
# Mock SSE payloads (mirror routers/draft.py + inquire stream shapes)
# ---------------------------------------------------------------------------


def _empty_annotations() -> dict:
    return {"redhat": [], "z3": []}


def sample_document(*, content: str = "Q3 revenue reached $12M.") -> dict:
    return {
        "document_id": "doc-default",
        "meta": {"project_id": "default", "title": "Simulation Doc"},
        "truth_ledger": {"revenue": 12_000_000},
        "body": [
            {
                "type": "section",
                "id": SECTION_ID,
                "title": "Revenue",
                "children": [
                    {
                        "type": "paragraph",
                        "id": NODE_ID,
                        "content": content,
                        "entities_referenced": ["revenue"],
                        "meta": {},
                        "annotations": _empty_annotations(),
                    }
                ],
                "meta": {},
                "annotations": _empty_annotations(),
            }
        ],
    }


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def draft_stream_success(*, content: str = "Q3 revenue reached $12M.") -> str:
    """Full compile + audit SSE sequence for POST .../draft/stream."""
    doc = sample_document(content=content)
    section_nodes = doc["body"]
    return (
        _sse("token", {"type": "token", "delta": "Streaming draft…"})
        + _sse(
            "compiled",
            {
                "type": "compiled",
                "nodes": section_nodes,
                "locks": [],
                "document": doc,
                "node_count": 1,
                "lock_count": 0,
                "draft_text": content,
            },
        )
        + _sse(
            "audit_complete",
            {
                "type": "audit_complete",
                "document": doc,
                "z3_results": {"z3_status": "PASS", "status": "PASS", "locks_verified": 1},
                "redhat_critiques": [],
                "redhat_count": 0,
                "gate_status": "pass",
                "z3_status": "PASS",
            },
        )
        + "data: [DONE]\n\n"
    )


def inquire_stream_success(*, new_content: str) -> str:
    """Refine-node SSE sequence for POST .../inquire/stream."""
    node = {
        "type": "paragraph",
        "id": NODE_ID,
        "content": new_content,
        "entities_referenced": ["revenue"],
        "meta": {},
        "annotations": _empty_annotations(),
    }
    return (
        _sse("token", {"type": "token", "delta": new_content})
        + _sse(
            "jdf_node_ready",
            {
                "type": "jdf_node_ready",
                "is_mutation": True,
                "target_node_id": NODE_ID,
                "original_content": "Q3 revenue reached $12M.",
                "new_content": new_content,
                "node": node,
            },
        )
        + _sse("complete", {"type": "complete", "ok": True})
        + "data: [DONE]\n\n"
    )


def _fulfill_sse(route, body: str) -> None:
    route.fulfill(
        status=200,
        headers={
            "Content-Type": "text/event-stream; charset=utf-8",
            "Cache-Control": "no-cache",
        },
        body=body,
    )


def _route_draft_success(route) -> None:
    if route.request.method != "POST":
        route.continue_()
        return
    _fulfill_sse(route, draft_stream_success())


def _route_inquire_success(route) -> None:
    if route.request.method != "POST":
        route.continue_()
        return
    _fulfill_sse(route, inquire_stream_success(new_content="Q3 revenue reached $15M, verified."))


def _route_abort_counter(max_abort: int, success_body: str):
    """Abort the first *max_abort* POSTs, then fulfill with *success_body*."""
    state = {"hits": 0}

    def handler(route):
        if route.request.method != "POST":
            route.continue_()
            return
        state["hits"] += 1
        if state["hits"] <= max_abort:
            route.abort("failed")
        else:
            _fulfill_sse(route, success_body)

    return handler, state


@pytest.fixture
def workbench_page(page, base_url):
    """Open /app on desktop with the tour dismissed and compile counter at 0."""
    _prime(page, compiles=0)
    return _goto_workbench(page, base_url)


def _committed_version(text: str) -> int | None:
    match = re.search(r"Committed \(v(\d+)\)", text or "")
    return int(match.group(1)) if match else None


def _expect_beforeunload(page) -> None:
    """Assert AssureUnsaved's beforeunload listener prevents default.

    Chromium headless (and ``channel=chrome``) often never surfaces a native
    ``dialog.type == 'beforeunload'`` event — ``page.close(run_before_unload=True)``
    and ``location.href`` either no-op or deadlock. Dispatching a cancelable
    ``beforeunload`` event exercises the same listener (preventDefault +
    returnValue) that the browser would call.
    """
    dialog_types: list[str] = []

    def on_dialog(dialog) -> None:
        dialog_types.append(dialog.type)
        dialog.dismiss()

    page.on("dialog", on_dialog)
    prevented = page.evaluate(
        """() => {
          const e = new Event('beforeunload', { cancelable: true });
          window.dispatchEvent(e);
          return e.defaultPrevented === true;
        }"""
    )
    assert prevented or "beforeunload" in dialog_types


# ---------------------------------------------------------------------------
# 1. Initial load & draft persistence (assure_unsaved.js)
# ---------------------------------------------------------------------------


def test_draft_persists_across_reload_without_save(workbench_page):
    """Type in #generate-intent; after ≥1.2s debounce, reload restores assure_draft_prompt.

    Storage shape is ``{compile, refine}``. Inner ``.app-logo`` is a div, not a link.
    """
    page = workbench_page
    logo = page.locator(APP_LOGO).first
    assert logo.evaluate("el => el.tagName") != "A"
    assert page.locator(f"{APP_LOGO} a").count() == 0

    page.fill(COMPILE_INPUT, COMPLEX_PROMPT)
    page.wait_for_timeout(DRAFT_DEBOUNCE_WAIT_MS)

    stored = page.evaluate(
        f"() => JSON.parse(localStorage.getItem('{DRAFT_STORAGE_KEY}') || '{{}}')"
    )
    assert stored.get("compile") == COMPLEX_PROMPT

    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(COMPILE_INPUT, state="visible")

    restored = page.input_value(COMPILE_INPUT)
    assert restored == COMPLEX_PROMPT

    after_reload = page.evaluate(
        f"() => JSON.parse(localStorage.getItem('{DRAFT_STORAGE_KEY}') || '{{}}')"
    )
    assert after_reload.get("compile") == COMPLEX_PROMPT


# ---------------------------------------------------------------------------
# 2. Compilation & stream lock (beforeunload while isGenerating / unsaved)
# ---------------------------------------------------------------------------


def test_beforeunload_fires_during_draft_stream(workbench_page):
    """Click Compile, navigate away during SSE; beforeunload dialog must appear.

    ``assure_unsaved.js`` fires when ``hasUnsavedChanges || isGenerating``.
    Stream URL is POST ``/api/projects/default/draft/stream`` (not /compile).
    """
    page = workbench_page

    pending: list = []

    def hold_stream(route):
        if route.request.method == "POST" and "/draft/stream" in route.request.url:
            pending.append(route)
            return
        route.continue_()

    page.route("**/draft/stream", hold_stream)
    _fill_and_compile(page, COMPLEX_PROMPT)
    page.wait_for_function("() => window.isGenerating === true", timeout=10_000)

    _expect_beforeunload(page)

    for route in pending:
        try:
            route.abort("aborted")
        except Exception:
            pass


def test_beforeunload_fires_when_prompt_unsaved(workbench_page):
    """Dirty #generate-intent (hasUnsavedChanges) also triggers beforeunload."""
    page = workbench_page
    page.locator(COMPILE_INPUT).fill(COMPLEX_PROMPT)
    page.wait_for_function("() => window.hasUnsavedChanges === true", timeout=10_000)
    _expect_beforeunload(page)


# ---------------------------------------------------------------------------
# 3. Red-Hat audit & refine loop (mocked SSE + real JDF save)
# ---------------------------------------------------------------------------


def test_compile_dock_refine_updates_ast_and_save_pill(workbench_page):
    """Compile → dock (Committed vN) → Red-Hat refine → AST + save pill advance.

    Save pill ``#save-status`` becomes ``Committed (vN)`` only after dock/PUT,
    not when the SSE stream finishes (that path may show ``Stream complete``).
    """
    page = workbench_page
    page.route("**/draft/stream", _route_draft_success)
    page.route("**/inquire/stream", _route_inquire_success)

    _fill_and_compile(page, COMPLEX_PROMPT)

    page.wait_for_selector(ACCEPT_DOCK_BTN, state="visible", timeout=30_000)
    page.wait_for_function(
        f"() => !document.querySelector('{ACCEPT_DOCK_BTN}').disabled",
        timeout=30_000,
    )
    # Stream complete ≠ persisted revision.
    pre_dock = page.locator(SAVE_PILL).inner_text()
    assert _committed_version(pre_dock) is None

    page.click(ACCEPT_DOCK_BTN)

    page.wait_for_function(
        f"() => /Committed \\(v\\d+\\)/.test(document.querySelector('{SAVE_PILL}').textContent)",
        timeout=15_000,
    )
    version_after_dock = _committed_version(page.locator(SAVE_PILL).inner_text())
    assert version_after_dock is not None and version_after_dock >= 1

    # Red-Hat toggle (#toggle-redhat) defaults checked in index.html.
    assert page.is_checked(REDHAT_TOGGLE)

    page.locator(CANVAS_NODE).first.click()
    page.wait_for_selector("#active-target-id", state="visible")
    page.fill(INQUIRY_INPUT, "Tighten revenue wording and cite verification.")
    page.click(INQUIRE_BTN)

    page.wait_for_selector(DIFF_ACCEPT, state="visible", timeout=20_000)
    page.click(DIFF_ACCEPT)

    page.wait_for_function(
        f"""() => {{
          const t = document.querySelector('{SAVE_PILL}').textContent;
          const m = /Committed \\(v(\\d+)\\)/.exec(t);
          return m && Number(m[1]) > {version_after_dock};
        }}""",
        timeout=15_000,
    )

    node_text = page.locator(f'[data-node-id="{NODE_ID}"] .jdf-node-body').inner_text()
    assert "$15M" in node_text


# ---------------------------------------------------------------------------
# 4. Error handling — SSE abort / reconnect (generate.js + AssureSse.postStream)
# ---------------------------------------------------------------------------


def test_draft_stream_reconnect_toast_then_recovery(workbench_page):
    """Abort POST /draft/stream until generate.js outer retry; toast then success.

    Inner retries live in ``AssureSse.postStream`` (2s base, 3 tries). After those
    fail, generate.js toasts ``Connection dropped — retrying…`` and retries once.
    """
    page = workbench_page
    handler, state = _route_abort_counter(3, draft_stream_success())
    page.route("**/draft/stream", handler)

    _fill_and_compile(page, COMPLEX_PROMPT)

    page.wait_for_selector(
        f"{TOAST}:has-text('{RECONNECT_TOAST}'), {TOAST}:has-text('retrying'), {TOAST}:has-text('Retrying')",
        timeout=20_000,
    )
    page.wait_for_function(
        f"() => !document.querySelector('{ACCEPT_DOCK_BTN}').disabled",
        timeout=30_000,
    )
    assert state["hits"] >= 4


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Draft /draft/stream uses AssureSse.postStream (2s base exp. backoff, 3 tries) "
        "plus a single generate.js retry at fixed 800ms — not a uniform exponential "
        "backoff end-to-end. Timing assertion is flaky in CI; see test_draft_stream_reconnect_toast."
    ),
)
def test_draft_stream_exponential_backoff_timing(workbench_page):
    """Document expected postSseStream backoff gaps (2s, 4s) — may flake on slow hosts."""
    page = workbench_page
    timestamps: list[float] = []

    def abort_and_log(route):
        if route.request.method != "POST":
            route.continue_()
            return
        timestamps.append(time.monotonic())
        if len(timestamps) <= 3:
            route.abort("failed")
        else:
            _fulfill_sse(route, draft_stream_success())

    page.route("**/draft/stream", abort_and_log)
    _fill_and_compile(page, COMPLEX_PROMPT)

    page.wait_for_function(
        f"() => !document.querySelector('{ACCEPT_DOCK_BTN}').disabled",
        timeout=45_000,
    )
    assert len(timestamps) >= 4
    gap1 = timestamps[1] - timestamps[0]
    gap2 = timestamps[2] - timestamps[1]
    assert gap1 >= 1.5
    assert gap2 >= 3.5


def test_draft_stream_error_toast_when_all_retries_fail(workbench_page):
    """After postSseStream (3) + generate.js retry (1), show error toast."""
    page = workbench_page

    def always_abort(route):
        if route.request.method == "POST" and "/draft/stream" in route.request.url:
            route.abort("failed")
        else:
            route.continue_()

    page.route("**/draft/stream", always_abort)
    _fill_and_compile(page, COMPLEX_PROMPT)

    page.wait_for_selector(f"{TOAST}.toast-error, {TOAST}.toast-info", timeout=45_000)
    toast_text = page.locator(TOAST).last.inner_text()
    assert toast_text
    assert re.search(r"retry|fail|Stream|error", toast_text, re.I)


# ---------------------------------------------------------------------------
# 5. Mobile lockout — CSS max-width 1024px
# ---------------------------------------------------------------------------


def test_mobile_viewport_shows_lockout_and_hides_workbench(page, base_url):
    """At 390×844 the workbench is hidden; #mobile-lockout is the only UI.

    Production testers on phones never reach #generate-intent. Desktop fixtures
    must stay ≥1025px so this does not fire accidentally.
    """
    _prime(page, viewport=MOBILE_VIEWPORT)
    _goto_workbench(page, base_url, wait_canvas=False)

    page.wait_for_selector(MOBILE_LOCKOUT, state="visible")
    assert page.locator(MOBILE_LOCKOUT).is_visible()
    assert page.locator(ASSURE_APP).is_hidden()
    assert page.locator(WORKBENCH).is_hidden()
    assert page.locator(COMPILE_BTN).is_hidden()


# ---------------------------------------------------------------------------
# 6. Tab guard — BroadcastChannel('assure_workbench_state')
# ---------------------------------------------------------------------------


def test_second_tab_shows_workbench_lockout(workbench_page, context, base_url):
    """A second page on the same origin is frozen by #tab-lockout-modal.

    First tab answers ``ping`` with ``pong`` after PING_TIMEOUT_MS (400). The
    second tab shows the modal; the first stays usable.
    """
    page1 = workbench_page
    assert page1.locator(TAB_LOCKOUT).is_hidden()
    page1.wait_for_timeout(TAB_GUARD_ESTABLISH_MS + 50)

    page2 = context.new_page()
    _prime(page2)
    _goto_workbench(page2, base_url, wait_canvas=False)
    page2.wait_for_selector(TAB_LOCKOUT, state="visible", timeout=8_000)
    assert page2.locator(TAB_LOCKOUT).is_visible()
    assert page1.locator(TAB_LOCKOUT).is_hidden()
    page2.close()


# ---------------------------------------------------------------------------
# 7. Session compile cap — AssureSessionLimit / assure_session_compiles
# ---------------------------------------------------------------------------


def test_session_compile_cap_locks_button_at_fifteen(page, base_url):
    """15 compiles per tab (sessionStorage ``assure_session_compiles``).

    First paint at count=15: ``AssureSessionLimit.applyUi`` disables Compile
    with ``Session Limit Reached``. Then reset to 14 and ``tryConsume`` the
    last slot — same lock, without depending on a live SSE round-trip.
    """
    _prime(page, compiles=SESSION_COMPILE_LIMIT)
    _goto_workbench(page, base_url)
    # i18n reapplies data-i18n="generate.compile" after DOMContentLoaded; re-run applyUi.
    page.evaluate("() => window.AssureSessionLimit && window.AssureSessionLimit.applyUi()")

    btn = page.locator(COMPILE_BTN)
    assert btn.is_disabled()
    assert SESSION_LIMIT_LABEL in btn.inner_text()
    badge = page.locator(SESSION_BADGE)
    assert badge.is_visible()
    assert SESSION_LIMIT_LABEL in badge.inner_text()

    page.evaluate(
        """() => {
          sessionStorage.setItem('assure_session_compiles', '14');
          window.AssureSessionLimit.applyUi();
        }"""
    )
    assert page.evaluate("() => window.AssureSessionLimit.remaining()") == 1
    assert btn.is_enabled()

    consumed = page.evaluate("() => window.AssureSessionLimit.tryConsume()")
    assert consumed is True
    assert page.evaluate("() => window.AssureSessionLimit.getCount()") == SESSION_COMPILE_LIMIT
    assert btn.is_disabled()
    assert SESSION_LIMIT_LABEL in btn.inner_text()
    assert SESSION_LIMIT_LABEL in badge.inner_text()

    # Further compile clicks must not POST /draft/stream.
    posts = {"n": 0}

    def count_post(route):
        if route.request.method == "POST" and "/draft/stream" in route.request.url:
            posts["n"] += 1
            route.abort("failed")
            return
        route.continue_()

    page.route("**/draft/stream", count_post)
    page.fill(COMPILE_INPUT, COMPLEX_PROMPT)
    btn.click(force=True)
    page.wait_for_timeout(400)
    assert posts["n"] == 0
    assert page.evaluate("() => window.AssureSessionLimit.getCount()") == SESSION_COMPILE_LIMIT
