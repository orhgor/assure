"""Playwright coverage for the Argument Spine: tree from the live JDF,
Thesis label, click-to-scroll, Z3 violation dots, fold persistence.

Injects a document into ``window.__assureJdf`` so no model call is needed.

Run locally:
  uv sync --extra dev
  uv run pytest tests/e2e/test_argument_spine_e2e.py -v --tb=short
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.playwright

SPINE = "#argument-spine"
SPINE_TREE = "#argument-spine-tree"
SPINE_EMPTY = "#argument-spine-empty"
SPINE_ROW = "#argument-spine-tree .spine-row"
THESIS_ROW = "#argument-spine-tree .spine-section.is-thesis .spine-section-row"
WORKBENCH = "#jdf-workbench"
COMPILE_BTN = "#generate-compile-btn"
LOCALE_SELECT = "#locale-select"
ONBOARDING_KEY = "assure_onboarding_complete"

SAMPLE_TREE = {
    "document_id": "doc-spine-e2e",
    "meta": {"title": "Spine fixture"},
    "truth_ledger": {"revenue": 12},
    "body": [
        {
            "type": "section",
            "id": "sec-thesis",
            "title": "Executive Summary",
            "children": [
                {
                    "type": "paragraph",
                    "id": "p-ok",
                    "content": "Revenue reached 12 this quarter.",
                    "entities_referenced": [],
                    "provenance": [],
                    "meta": {},
                    "annotations": {"redhat": [], "z3": []},
                },
                {
                    "type": "paragraph",
                    "id": "p-z3",
                    "content": "The runway figure contradicts the ledger.",
                    "entities_referenced": [],
                    "provenance": [],
                    "meta": {},
                    "annotations": {
                        "redhat": [],
                        "z3": [
                            {
                                "id": "z3-1",
                                "message": "contradicts ledger",
                                "status": "violation",
                                "canonical_key": "runway",
                            }
                        ],
                    },
                },
            ],
            "meta": {},
            "annotations": {"redhat": [], "z3": []},
        },
        {
            "type": "section",
            "id": "sec-branch",
            "title": "Risks",
            "children": [
                {
                    "type": "paragraph",
                    "id": "p-rh",
                    "content": "An unsupported claim sits here.",
                    "entities_referenced": [],
                    "provenance": [],
                    "meta": {},
                    "annotations": {
                        "redhat": [{"id": "rh-1", "text": "Unsupported.", "status": "open"}],
                        "z3": [],
                    },
                }
            ],
            "meta": {},
            "annotations": {"redhat": [], "z3": []},
        },
    ],
}


def _app_url(base_url: str) -> str:
    env_base = os.environ.get("ASSURE_BASE_URL")
    root = (env_base or base_url).rstrip("/")
    return f"{root}/app"


def _goto_workbench(page, base_url: str):
    page.add_init_script(f"try {{ localStorage.setItem({ONBOARDING_KEY!r}, '1'); }} catch (e) {{}}")
    page.goto(_app_url(base_url), wait_until="domcontentloaded")
    page.wait_for_selector(WORKBENCH, state="visible")
    page.wait_for_function(
        "() => window.__assureJdf && typeof window.__assureJdf.render === 'function'"
    )
    page.wait_for_function("() => !document.body.classList.contains('onboarding-active')")
    page.wait_for_selector(COMPILE_BTN, state="visible")
    page.wait_for_function("() => !!window.AssureArgumentSpine")
    return page


def _load_tree(page):
    page.evaluate(
        """(tree) => {
            const mgr = window.__assureJdf;
            mgr.tree = tree;
            mgr.render();
        }""",
        SAMPLE_TREE,
    )
    page.wait_for_selector(SPINE_TREE, state="visible")


class TestSpineEmptyAndI18n:
    def test_empty_state_before_document(self, page, base_url):
        _goto_workbench(page, base_url)
        page.evaluate(
            """() => {
                const mgr = window.__assureJdf;
                mgr.tree = { document_id: 'empty', meta: {}, truth_ledger: {}, body: [] };
                mgr.render();
            }"""
        )
        page.wait_for_selector(SPINE_EMPTY, state="visible")
        assert page.locator(SPINE).is_visible()

    def test_locale_switch_translates_empty_state(self, page, base_url):
        _goto_workbench(page, base_url)
        page.evaluate(
            """() => {
                const mgr = window.__assureJdf;
                mgr.tree = { document_id: 'empty', meta: {}, truth_ledger: {}, body: [] };
                mgr.render();
            }"""
        )
        page.select_option(LOCALE_SELECT, "tr")
        page.wait_for_function("() => document.documentElement.lang === 'tr'")
        empty = page.locator(SPINE_EMPTY).inner_text()
        assert "derleyin" in empty
        assert "Compile a document" not in empty


class TestSpineTree:
    def test_thesis_label_and_node_count(self, page, base_url):
        _goto_workbench(page, base_url)
        _load_tree(page)
        thesis = page.locator(THESIS_ROW).inner_text()
        assert "Thesis" in thesis
        assert "Executive Summary" not in thesis
        # 2 section rows + 3 nodes
        assert page.locator(SPINE_ROW).count() == 5

    def test_z3_violation_is_red_on_spine_and_gutter(self, page, base_url):
        """Regression: backend status is 'violation', not 'FAIL'."""
        _goto_workbench(page, base_url)
        _load_tree(page)
        status = page.evaluate(
            "() => window.__assureJdf.computeNodeStatus(window.__assureJdf.getNodeById('p-z3'))"
        )
        assert status == "error"
        gutter_class = page.locator('.verification-gutter[data-node-id="p-z3"]').get_attribute(
            "class"
        )
        if gutter_class:
            assert "error" in gutter_class
        assert (
            page.locator(
                '#argument-spine-tree .spine-row[data-node-id="p-z3"] .spine-dot-error'
            ).count()
            == 1
        )
        assert (
            page.locator(
                '#argument-spine-tree .spine-row[data-node-id="p-rh"] .spine-dot-warning'
            ).count()
            == 1
        )
        assert (
            page.locator('#argument-spine-tree .spine-row[data-node-id="p-ok"] .spine-lock').count()
            == 1
        )

    def test_click_marks_active_and_selects_canvas_node(self, page, base_url):
        _goto_workbench(page, base_url)
        _load_tree(page)
        page.locator('.spine-row[data-node-id="p-ok"]').click()
        page.wait_for_function("() => window.__assureJdf.surgicalTargetId === 'p-ok'")
        assert page.locator('.spine-row[data-node-id="p-ok"]').evaluate(
            "el => el.classList.contains('spine-active')"
        )
        generate = page.locator("#view-generate")
        assert generate.evaluate("el => !el.hidden")

    def test_section_fold_persists_across_reload(self, page, base_url):
        _goto_workbench(page, base_url)
        _load_tree(page)
        page.locator(".spine-section.is-thesis .spine-fold").click()
        page.wait_for_function(
            "() => (JSON.parse(localStorage.getItem('assure_spine_folded_sections') || '[]')).indexOf('sec-thesis') >= 0"
        )
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(WORKBENCH, state="visible")
        page.wait_for_function("() => !!window.AssureArgumentSpine")
        _load_tree(page)
        expanded = page.locator(".spine-section.is-thesis").get_attribute("aria-expanded")
        assert expanded == "false"
        assert page.locator('.spine-row[data-node-id="p-ok"]').count() == 0
