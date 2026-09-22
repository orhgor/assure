"""Shared selectors, SSE mocks, and workbench helpers for Playwright tests."""

from __future__ import annotations

import json
import os
import time
from typing import Any

ONBOARDING_KEY = "assure_onboarding_complete"
DISCLAIMER_KEY = "assure_disclaimer_ack"
SESSION_COMPILE_KEY = "assure_session_compiles"
DESKTOP_VIEWPORT = {"width": 1440, "height": 900}

COMPILE_INPUT = "#generate-intent"
COMPILE_BTN = "#generate-compile-btn"
FULL_AUDIT_BTN = "#generate-full-audit-btn"
DOCK_BTN = "#generate-accept-dock-phase"
WORKBENCH = "#workbench-root"
GATE_STATUS = "#gate-status-text"
COMPILE_STATUS_BAR = ".workbench-status-bar"
CONFIDENCE_WRAP = "#confidence-overlay-wrap"
CONFIDENCE_TOGGLE = "#confidence-overlay-toggle"
RENDER_TARGET = "#jdf-render-target"
VERSION_SLIDER = "#version-history-slider"
VERSION_DISPLAY = "#version-display"
DIFF_PANEL = "#jdf-diff-panel"
LASER_BEAM = "#laser-beam"
INK_STAMP = ".ink-stamp"
DIFF_XRAY = "#diff-xray-overlay"
REASONING_GRAPH_BTN = "#wow-reasoning-graph-btn"
REASONING_GRAPH_DRAWER = "#reasoning-graph-drawer"
SURGICAL_POPOVER = "#jdf-surgical-popover"
REFINE_BTN = "#surgical-refine-ai-btn"
REFINE_INSTRUCTION = "#surgical-refine-instruction"
REFINE_FORM = "#jdf-surgical-popover-form"

PROVENANCE_PANEL = "#provenance-panel-drawer"
PROVENANCE_INFO_BTN = ".provenance-info-btn"
ROLE_SWITCHER = "#role-switcher"
SHOW_ADVANCED = "#workbench-show-advanced"
ACTION_PHASE = ".action-phase"

NODE_ID = "para-pw-1"
SECTION_ID = "sec-pw-1"


def app_url(base_url: str) -> str:
    root = (os.environ.get("ASSURE_BASE_URL") or base_url).rstrip("/")
    return f"{root}/app"


def prime_page(page, *, compiles: int = 0, wow_effects: bool = True) -> None:
    page.set_viewport_size(DESKTOP_VIEWPORT)
    wow = "true" if wow_effects else "false"
    page.add_init_script(
        f"""
        try {{
          localStorage.setItem({ONBOARDING_KEY!r}, '1');
          localStorage.setItem({DISCLAIMER_KEY!r}, '1');
          sessionStorage.setItem({SESSION_COMPILE_KEY!r}, '{int(compiles)}');
          window.__ASSURE_WOW_EFFECTS__ = {wow};
        }} catch (e) {{}}
        """
    )


def goto_workbench(page, base_url: str):
    page.goto(app_url(base_url), wait_until="domcontentloaded")
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


def goto_founder_workbench(page, base_url: str):
    """Open the founder-mode workbench shell.

    The founder shell is a localStorage-flagged default (`assure_founder_workbench`)
    — set it before load, then wait for the shell classes the founder tests
    assert on. Used by tests that exercise the founder shell rather than the
    legacy workbench.
    """
    page.add_init_script(
        """
        try {
          localStorage.setItem('assure_onboarding_complete', '1');
          localStorage.setItem('assure_founder_workbench', '1');
        } catch (e) {}
        """
    )
    page.goto(app_url(base_url), wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_selector("#workbench-root", state="visible", timeout=30_000)
    page.wait_for_function(
        "() => document.body.classList.contains('founder-workbench') && "
        "document.body.classList.contains('founder-mode-active')",
        timeout=10_000,
    )
    return page


def enter_compiler(page, project_id: str | None = None):
    """Enter the compiler (generate) view from the /app shell.

    Callers either navigate to `/app` (optionally `?project=<id>`) first or
    rely on this helper to wait for the nav, then switch the view and wait
    until the compile control is interactable.
    """
    page.wait_for_function("() => window.AssureNav && window.AssureProjects")
    page.evaluate(
        "() => window.AssureNav && window.AssureNav.switchView('generate', {replaceHash: false, persist: false})"
    )
    if project_id:
        page.evaluate(
            """(pid) => {
              window.__ASSURE_PROJECT_ID__ = pid;
            }""",
            project_id,
        )
    page.wait_for_selector(COMPILE_BTN, state="visible")
    return page


def click_full_audit(page):
    """Trigger the Full Audit action from the compiler toolbar."""
    page.locator(FULL_AUDIT_BTN).click()


def empty_annotations() -> dict[str, list]:
    return {"redhat": [], "z3": []}


def sample_document(*, content: str = "Revenue reached $12M in Q3.") -> dict[str, Any]:
    return {
        "document_id": "doc-playwright",
        "meta": {"project_id": "default", "title": "Playwright Doc"},
        "truth_ledger": {"revenue": 12_000_000},
        "body": [
            {
                "type": "section",
                "id": SECTION_ID,
                "title": "Overview",
                "children": [
                    {
                        "type": "paragraph",
                        "id": NODE_ID,
                        "content": content,
                        "entities_referenced": ["revenue"],
                        "provenance": [
                            {
                                "source_type": "internal_doc",
                                "source_name": "NAIC_Underwriting_Policy_2025.pdf",
                                "source_id": "sub-pw-1",
                                "page_number": "4",
                                "extracted_quote": "Revenue reached $12M",
                                "url_or_doi": "",
                                "accessed_date": "",
                            }
                        ],
                        "meta": {},
                        "annotations": empty_annotations(),
                    }
                ],
                "meta": {},
                "annotations": empty_annotations(),
            }
        ],
    }


def sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def draft_stream_success(
    *,
    content: str = "Revenue reached $12M in Q3.",
    cache_hit: bool = False,
    confidence_spans: list | None = None,
) -> str:
    doc = sample_document(content=content)
    if confidence_spans:
        doc["meta"]["confidenceSpans"] = confidence_spans
    section_nodes = doc["body"]
    extra = {"cache_hit": True, "omp_cached": True} if cache_hit else {}
    from prompt_matrix.services.audit_summary import build_audit_summary

    audit = build_audit_summary(
        z3_results={
            "status": "PASS",
            "violations": [],
            "lock_results": [{"key": "revenue", "ok": True, "value": 12_000_000}],
        },
        redhat_critiques=[],
        document=doc,
    )
    verified = {
        "type": "verified",
        **audit,
        "z3_results": {"z3_status": "PASS", "status": "PASS", "locks_verified": 1},
        "redhat_critiques": [],
        "redhat_count": 0,
        "gate_status": "pass",
        "z3_status": "PASS",
        **extra,
    }
    return (
        sse("token", {"type": "token", "delta": "Streaming draft…"})
        + sse(
            "compiled",
            {
                "type": "compiled",
                "nodes": section_nodes,
                "locks": [],
                "document": doc,
                "node_count": 1,
                "lock_count": 0,
                "draft_text": content,
                **extra,
            },
        )
        + sse("verified", verified)
        + sse("complete", {"type": "complete", "ok": True, "node_count": 1, "lock_count": 0})
        + "data: [DONE]\n\n"
    )


def redhat_audit_stream_success(*, content: str = "Revenue reached $12M in Q3.") -> str:
    doc = sample_document(content=content)
    return (
        sse("status", {"type": "status", "message": "Running Stress Test…"})
        + sse(
            "audit_complete",
            {
                "type": "audit_complete",
                "document": doc,
                "z3_results": {"z3_status": "PASS", "status": "PASS", "locks_verified": 1},
                "redhat_critiques": [
                    {
                        "node_id": NODE_ID,
                        "claim": content,
                        "critique": "Wording is acceptable.",
                        "severity": "low",
                    }
                ],
                "redhat_count": 1,
                "gate_status": "pass",
                "z3_status": "PASS",
            },
        )
        + sse("complete", {"type": "complete", "ok": True, "redhat_count": 1})
        + "data: [DONE]\n\n"
    )


def fulfill_sse(route, body: str) -> None:
    route.fulfill(
        status=200,
        headers={
            "Content-Type": "text/event-stream; charset=utf-8",
            "Cache-Control": "no-cache",
        },
        body=body,
    )


def route_draft_success(
    route, *, cache_hit: bool = False, confidence_spans: list | None = None
) -> None:
    if route.request.method != "POST":
        route.continue_()
        return
    fulfill_sse(route, draft_stream_success(cache_hit=cache_hit, confidence_spans=confidence_spans))


def route_redhat_success(route) -> None:
    if route.request.method != "POST":
        route.continue_()
        return
    fulfill_sse(route, redhat_audit_stream_success())


def mock_sse_stream(page, *, cache_hit: bool = True) -> None:
    """Intercept draft/stream SSE and return a canned compile response."""

    def _handler(route) -> None:
        if route.request.method != "POST":
            route.continue_()
            return
        fulfill_sse(route, draft_stream_success(cache_hit=cache_hit))

    page.route("**/api/projects/*/draft/stream", _handler)


def fill_and_compile(page, prompt: str) -> None:
    page.locator(COMPILE_INPUT).fill(prompt)
    click_workbench(page, COMPILE_BTN)


def wait_compile_ready(page, timeout_ms: int = 60_000) -> None:
    page.wait_for_function(
        f"""() => {{
          const dock = document.querySelector('{DOCK_BTN}');
          const btn = document.querySelector('{COMPILE_BTN}');
          const compiling = document.getElementById('generate-compiling');
          if (btn && btn.disabled) return false;
          if (compiling && !compiling.hidden) return false;
          return dock && !dock.disabled;
        }}""",
        timeout=timeout_ms,
    )


def wait_audit_complete(page, timeout_ms: int = 60_000) -> None:
    page.wait_for_function(
        "() => window.AssureGenerate && window.AssureGenerate.auditComplete === true",
        timeout=timeout_ms,
    )


def dock_document(page) -> None:
    click_workbench(page, DOCK_BTN)
    page.wait_for_selector(
        f"{RENDER_TARGET} .jdf-tiptap-host, {RENDER_TARGET} [data-node-id]",
        state="visible",
        timeout=20_000,
    )


def click_workbench(page, selector: str) -> None:
    """Click through stacked panes (left pane / footer often intercept in headless)."""
    loc = page.locator(selector)
    page.evaluate(
        """() => {
          const panel = document.getElementById('view-generate');
          if (panel) panel.scrollIntoView({ block: 'nearest' });
        }"""
    )
    loc.scroll_into_view_if_needed()
    try:
        loc.click(timeout=5_000)
    except Exception:
        loc.click(force=True)


def confidence_spans_sample() -> list[dict[str, Any]]:
    return [
        {
            "nodeId": NODE_ID,
            "start": 0,
            "end": 7,
            "score": 0.92,
            "source": "z3",
            "reason": "Ledger revenue lock matches.",
        }
    ]
