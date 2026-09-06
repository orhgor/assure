"""JDF sync via @uurtech/jdf bridge."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.playwright


def test_jdf_apply_compiled_ast_sets_idle_state(workbench_page):
    page = workbench_page
    sample = {
        "document_id": "doc-jdf-sync",
        "meta": {"title": "Sync test"},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "sec-sync",
                "title": "Overview",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "p-sync",
                        "content": "Initial content",
                        "meta": {},
                        "annotations": {"redhat": [], "z3": []},
                    }
                ],
                "meta": {},
                "annotations": {"redhat": [], "z3": []},
            }
        ],
    }
    page.evaluate(
        """(ast) => {
          if (window.applyCompiledASTToCanvas) window.applyCompiledASTToCanvas(ast);
        }""",
        sample,
    )
    page.wait_for_selector("#workbench-root[data-state='idle']", timeout=5000)
    state = page.locator("#workbench-root").get_attribute("data-state")
    assert state == "idle"
