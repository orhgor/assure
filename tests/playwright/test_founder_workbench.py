"""Founder workbench Phase 2 — Playwright E2E tests."""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import Page, expect

from tests.playwright.helpers import app_url, goto_founder_workbench

pytestmark = pytest.mark.playwright

RUNS_LIST_RE = re.compile(r"/api/runs(\?.*)?$")
RUNS_POST_RE = re.compile(r"/api/runs$")
REDHAT_RE = re.compile(r"/api/runs/[^/]+/redhat$")

SAMPLE_RUN = {
    "id": "run_pw_test01",
    "workspace_id": "default",
    "directive": "Investigate Q3 revenue narrative",
    "title": "Investigate Q3 revenue narrative",
    "model": "gemini",
    "status": "stamped",
    "unanchored": False,
    "lock_count": 1,
    "sources_used": [{"id": "sub-pw-1", "name": "brief.pdf"}],
    "extracted_locks": [
        {
            "canonical_key": "Revenue",
            "value": 12_000_000,
            "metric": "Revenue",
            "lock_hash": "abc123hash4567",
            "source_id": "sub-pw-1",
            "lock_index": 1,
            "page_coordinates": {"page": 1, "x": 0, "y": 0, "width": 100, "height": 24},
        }
    ],
    "content": {
        "document_id": "doc-pw-run",
        "meta": {"project_id": "default"},
        "truth_ledger": {"Revenue": 12_000_000},
        "body": [
            {
                "type": "section",
                "id": "sec-pw",
                "title": "Run",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "para-pw",
                        "content": "Revenue reached $12M in Q3.",
                        "meta": {},
                        "annotations": {"redhat": [], "z3": []},
                    }
                ],
            }
        ],
    },
    "created_at": "2026-01-01 00:00:00",
    "updated_at": "2026-01-01 00:00:00",
    "redhat_findings": [],
}


def _mock_runs_api(page: Page) -> None:
    runs = []

    def handle_runs(route):
        if route.request.method == "POST":
            body = json.loads(route.request.post_data or "{}")
            run = dict(SAMPLE_RUN)
            run["directive"] = body.get("directive", run["directive"])
            run["id"] = f"run_pw_{len(runs)+1:02d}"
            runs.insert(0, run)
            route.fulfill(
                status=201,
                content_type="application/json",
                body=json.dumps({"ok": True, "run": run}),
            )
            return
        ws = "default"
        m = re.search(r"workspace_id=([^&]+)", route.request.url)
        if m:
            ws = m.group(1)
        payload = {
            "ok": True,
            "runs": [r for r in runs if r.get("workspace_id") == ws],
            "count": len(runs),
        }
        route.fulfill(content_type="application/json", body=json.dumps(payload))

    page.route(
        RUNS_POST_RE,
        lambda route: handle_runs(route) if route.request.method == "POST" else route.continue_(),
    )
    page.route(RUNS_LIST_RE, handle_runs)


def _mock_substrate(page: Page) -> None:
    page.route(
        "**/api/projects/default/substrate/sub-pw-1",
        lambda route: route.fulfill(
            content_type="application/json",
            body=json.dumps(
                {
                    "id": "sub-pw-1",
                    "filename": "brief.pdf",
                    "extracted_text": "Revenue reached $12M in Q3 per internal brief.",
                    "page_count": 1,
                }
            ),
        ),
    )


def _dismiss_overlays(page: Page) -> None:
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    page.evaluate(
        """() => {
          const palette = document.getElementById('command-palette-overlay');
          if (palette) {
            palette.classList.remove('is-open');
            palette.setAttribute('aria-hidden', 'true');
          }
          const bar = document.getElementById('command-bar-overlay');
          if (bar) {
            bar.hidden = true;
            bar.setAttribute('aria-hidden', 'true');
          }
        }"""
    )


DRAFTS_GET_RE = re.compile(r"/api/drafts(\?.*)?$")


def test_default_founder_shell(page: Page, base_url: str):
    """Founder workbench is the default /app experience."""

    def handle_drafts(route):
        if route.request.method == "GET":
            route.fulfill(
                content_type="application/json",
                body=json.dumps({"ok": True, "draft": None}),
            )
            return
        route.continue_()

    page.route(DRAFTS_GET_RE, handle_drafts)
    page.add_init_script(
        """
        try {
          localStorage.setItem('assure_onboarding_complete', '1');
          localStorage.removeItem('assure_founder_workbench');
        } catch (e) {}
        """
    )
    page.goto(app_url(base_url), wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_selector("#workbench-root", state="visible", timeout=30_000)
    page.wait_for_function(
        "() => document.body.classList.contains('founder-workbench') && !document.body.classList.contains('legacy-workbench')",
        timeout=10_000,
    )
    expect(page.locator("#panel-runs")).to_be_visible()
    expect(page.locator("#founder-draft-shell")).to_be_visible()
    page.wait_for_function(
        "() => { const p = document.getElementById('founder-draft-placeholder'); return p && !p.hidden; }",
        timeout=15_000,
    )
    expect(page.locator("#founder-draft-placeholder")).to_contain_text("⌘K")
    expect(page.locator("#left-pane #generate-compile-btn")).to_have_count(0)
    expect(page.locator("#left-pane #panel-draft")).to_have_count(0)
    expect(page.locator("#founder-legacy-park #panel-draft")).to_have_count(1)
    expect(page.locator("#left-pane ~ nav#app-sidebar, #app-body #app-sidebar")).to_have_count(0)
    expect(page.locator("#founder-legacy-park #app-sidebar")).to_have_count(1)
    expect(page.locator("#left-pane .workbench-stepper-container")).to_have_count(0)
    expect(page.locator("#founder-cmdk-btn")).to_be_visible()
    expect(page.locator("#btn-export-dossier")).to_be_visible()
    expect(page.locator("#projects-dashboard")).to_be_hidden()
    expect(page.locator("#canvas-onboarding-state")).to_be_hidden()
    expect(page.locator("#panel-write")).to_be_hidden()
    expect(page.locator(".founder-sidebar-legacy").first).to_be_hidden()
    page.wait_for_function(
        """() => {
          const content = document.querySelector('.app-content');
          const container = document.querySelector('.app-container');
          const sidebar = document.querySelector('#app-body > #app-sidebar');
          if (!content || !container) return false;
          const contentCol = getComputedStyle(content).gridColumnStart;
          const cols = getComputedStyle(container).gridTemplateColumns.trim().split(/\\s+/);
          return contentCol === '1' && cols.length === 2 && !sidebar;
        }""",
        timeout=10_000,
    )
    layout = page.evaluate(
        """() => {
          const left = document.querySelector('.left-pane');
          const right = document.querySelector('.right-pane');
          return {
            leftWidth: left ? left.getBoundingClientRect().width : 0,
            rightWidth: right ? right.getBoundingClientRect().width : 0,
          };
        }"""
    )
    assert layout["leftWidth"] >= 280
    assert layout["rightWidth"] > layout["leftWidth"]


def test_command_bar(page: Page, base_url: str):
    _mock_runs_api(page)
    goto_founder_workbench(page, base_url)
    expect(page.locator("#command-bar-overlay")).to_be_hidden()
    page.keyboard.press("Meta+K")
    expect(page.locator("#command-bar-overlay")).to_be_visible()
    page.locator("#command-bar-input").fill("Investigate Q3 revenue narrative")
    page.keyboard.press("Enter")
    page.wait_for_selector(".run-card", timeout=15_000)
    _dismiss_overlays(page)
    expect(page.locator(".run-card")).to_contain_text("Investigate Q3 revenue")


def test_redhat(page: Page, base_url: str):
    runs = [dict(SAMPLE_RUN)]

    def handle_redhat(route):
        run_id = route.request.url.rstrip("/").split("/")[-2]
        findings = [
            {
                "id": "rh_pw_1",
                "run_id": run_id,
                "title": "Unsupported claim",
                "content": "Revenue figure lacks citation.",
                "severity": "high",
                "suggested_fix": "Add source citation.",
                "status": "open",
                "highlight_text": "Revenue reached",
                "model_used": "anthropic/claude-sonnet-4-5",
            }
        ]
        for run in runs:
            if run["id"] == run_id:
                run["redhat_findings"] = findings
        route.fulfill(
            content_type="application/json",
            body=json.dumps({"ok": True, "findings": findings, "count": 1}),
        )

    def handle_runs(route):
        if route.request.method == "GET":
            route.fulfill(
                content_type="application/json",
                body=json.dumps({"ok": True, "runs": runs, "count": len(runs)}),
            )
            return
        route.continue_()

    page.route(REDHAT_RE, handle_redhat)
    page.route(RUNS_LIST_RE, handle_runs)
    goto_founder_workbench(page, base_url)
    expect(page.locator(".run-card")).to_have_count(1, timeout=15_000)
    _dismiss_overlays(page)
    page.locator('[data-action="redhat"]').first.click(force=True)
    page.wait_for_selector(".run-findings-list", timeout=15_000)
    expect(page.locator(".run-finding-item")).to_contain_text("Unsupported claim")


def test_send_to_draft(page: Page, base_url: str):
    _mock_runs_api(page)
    goto_founder_workbench(page, base_url)
    page.keyboard.press("Meta+K")
    page.locator("#command-bar-input").fill("Revenue check")
    page.keyboard.press("Enter")
    page.wait_for_selector(".run-card", timeout=15_000)
    _dismiss_overlays(page)
    page.locator('[data-action="draft"]').first.click(force=True)
    page.wait_for_function(
        "() => document.querySelector('.lock-pill') || (document.querySelector('#founder-draft-editor') && document.querySelector('#founder-draft-editor').innerText.includes('🔒'))",
        timeout=15_000,
    )
    expect(page.locator(".lock-pill, #founder-draft-editor").first).to_contain_text("🔒")


def test_evidence_inspector(page: Page, base_url: str):
    _mock_runs_api(page)
    _mock_substrate(page)
    goto_founder_workbench(page, base_url)
    page.keyboard.press("Meta+K")
    page.locator("#command-bar-input").fill("Revenue check")
    page.keyboard.press("Enter")
    page.wait_for_selector(".run-card", timeout=15_000)
    _dismiss_overlays(page)
    page.locator('[data-action="draft"]').first.click(force=True)
    page.wait_for_function(
        "() => document.querySelector('.lock-pill') || (document.querySelector('#founder-draft-editor') && document.querySelector('#founder-draft-editor').innerText.includes('🔒'))",
        timeout=15_000,
    )
    page.evaluate(
        """() => {
          document.dispatchEvent(new CustomEvent('assure:lock-pill-click', {
            detail: {
              lockHash: 'abc123hash4567',
              sourceId: 'sub-pw-1',
              pageCoordinates: { page: 1, x: 0, y: 0, width: 100, height: 24 },
              lockIndex: 1,
            },
          }));
        }"""
    )
    expect(page.locator("#evidence-inspector-drawer")).to_be_visible()
    expect(page.locator("#evidence-inspector-hash")).not_to_have_text("")
    expect(page.locator("#evidence-inspector-body")).to_contain_text("Revenue reached")


def test_export(page: Page, base_url: str):
    pdf_marker = b"%PDF"

    page.route(
        "**/export?format=dossier-pdf**",
        lambda route: route.fulfill(
            content_type="application/pdf",
            body=pdf_marker + b"-mock-verification-dossier",
        ),
    )
    goto_founder_workbench(page, base_url)
    with page.expect_download() as dl_info:
        page.locator("#btn-export-dossier").click()
    download = dl_info.value
    assert download.suggested_filename.endswith(".pdf")
