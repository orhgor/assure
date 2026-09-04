"""Playwright crash / resilience simulation for the Assure JDF workbench.

Exercises client-side draft persistence, stream lock (beforeunload), compile +
Red-Hat refine loop, and SSE reconnect/error handling. SSE bodies are mocked via
``page.route`` so tests run without live model API keys.

Run locally:
  uv sync --extra dev
  playwright install chromium
  pytest tests/test_user_simulation.py -v

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
# Constants aligned with prompt_matrix/static/assure_unsaved.js
# ---------------------------------------------------------------------------

DRAFT_STORAGE_KEY = "assure_draft_prompt"
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

COMPLEX_PROMPT = (
    "Q3 investor update: revenue $12M ARR (+45% YoY), runway 18 months, "
    "include Red-Hat audit on growth claims vs industry benchmark."
)

NODE_ID = "para-sim-1"
SECTION_ID = "sec-sim-1"


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
    """Full compile + audit SSE sequence for /draft/stream."""
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
    """Refine-node SSE sequence for /inquire/stream."""
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
    """Open /app and wait for JDF canvas bootstrap."""
    env_base = os.environ.get("ASSURE_BASE_URL")
    root = env_base or base_url
    page.goto(f"{root}/app", wait_until="domcontentloaded")
    page.wait_for_selector("#jdf-workbench", state="visible")
    page.wait_for_function("() => window.__assureJdf && typeof window.__assureJdf.render === 'function'")
    return page


# ---------------------------------------------------------------------------
# 1. Initial load & draft persistence (assure_unsaved.js)
# ---------------------------------------------------------------------------


def test_draft_persists_across_reload_without_save(workbench_page):
    """Type in #generate-intent, reload; localStorage + textarea restore from assure_draft_prompt."""
    page = workbench_page
    page.fill(COMPILE_INPUT, COMPLEX_PROMPT)

    # assure_unsaved.js debounces writes by DEBOUNCE_MS (1000).
    page.wait_for_timeout(1200)

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
# 2. Compilation & stream lock (beforeunload while isGenerating)
# ---------------------------------------------------------------------------


def test_beforeunload_fires_during_draft_stream(workbench_page):
    """Click Compile, navigate away during SSE; beforeunload dialog must appear."""
    page = workbench_page
    page.fill(COMPILE_INPUT, COMPLEX_PROMPT)

    pending: list = []

    def hold_stream(route):
        if route.request.method == "POST" and route.request.url.endswith("/draft/stream"):
            pending.append(route)
            return
        route.continue_()

    page.route("**/draft/stream", hold_stream)
    page.click(COMPILE_BTN)
    page.wait_for_function("() => window.isGenerating === true")

    dialog_types: list[str] = []

    def on_dialog(dialog):
        dialog_types.append(dialog.type)
        dialog.dismiss()

    page.on("dialog", on_dialog)
    # Assigning location triggers beforeunload when AssureUnsaved.isGenerating is true.
    page.evaluate("() => { window.location.href = '/history'; }")
    page.wait_for_timeout(500)

    assert "beforeunload" in dialog_types

    for route in pending:
        route.abort("aborted")


# ---------------------------------------------------------------------------
# 3. Red-Hat audit & refine loop (mocked SSE + real JDF save)
# ---------------------------------------------------------------------------


def test_compile_dock_refine_updates_ast_and_save_pill(workbench_page):
    """Compile → dock (Committed v1) → Red-Hat refine → AST + save pill advance."""
    page = workbench_page
    page.route("**/draft/stream", _route_draft_success)
    page.route("**/inquire/stream", _route_inquire_success)

    page.fill(COMPILE_INPUT, COMPLEX_PROMPT)
    page.click(COMPILE_BTN)

    page.wait_for_selector(ACCEPT_DOCK_BTN, state="visible", timeout=30_000)
    page.wait_for_function(
        f"() => !document.querySelector('{ACCEPT_DOCK_BTN}').disabled",
        timeout=30_000,
    )
    page.click(ACCEPT_DOCK_BTN)

    page.wait_for_function(
        f"() => /Committed \\(v1\\)/.test(document.querySelector('{SAVE_PILL}').textContent)",
        timeout=15_000,
    )

    # Red-Hat toggle (#toggle-redhat) defaults checked in index.html.
    assert page.is_checked(REDHAT_TOGGLE)

    page.locator(CANVAS_NODE).first.click()
    page.wait_for_selector("#active-target-id", state="visible")
    page.fill(INQUIRY_INPUT, "Tighten revenue wording and cite verification.")
    page.click(INQUIRE_BTN)

    page.wait_for_selector(DIFF_ACCEPT, state="visible", timeout=20_000)
    page.click(DIFF_ACCEPT)

    page.wait_for_function(
        f"() => /Committed \\(v2\\)/.test(document.querySelector('{SAVE_PILL}').textContent)",
        timeout=15_000,
    )

    node_text = page.locator(f'[data-node-id="{NODE_ID}"] .jdf-node-body').inner_text()
    assert "$15M" in node_text


# ---------------------------------------------------------------------------
# 4. Error handling — SSE abort / reconnect (generate.js + AssureSse.postStream)
# ---------------------------------------------------------------------------


def test_draft_stream_reconnect_toast_then_recovery(workbench_page):
    """Block postSseStream attempts until generate.js outer retry; toast then success."""
    page = workbench_page
    handler, state = _route_abort_counter(3, draft_stream_success())
    page.route("**/draft/stream", handler)

    page.fill(COMPILE_INPUT, COMPLEX_PROMPT)
    page.click(COMPILE_BTN)

    page.wait_for_selector(
        f"{TOAST}:has-text('retrying'), {TOAST}:has-text('Retrying')",
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
    page.fill(COMPILE_INPUT, COMPLEX_PROMPT)
    page.click(COMPILE_BTN)

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
    page.fill(COMPILE_INPUT, COMPLEX_PROMPT)
    page.click(COMPILE_BTN)

    page.wait_for_selector(f"{TOAST}.toast-error, {TOAST}.toast-info", timeout=45_000)
    toast_text = page.locator(TOAST).last.inner_text()
    assert toast_text
    assert re.search(r"retry|fail|Stream|error", toast_text, re.I)
