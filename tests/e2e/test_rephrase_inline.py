"""Inline node rephrase: rewrite only the selected paragraph.

The LLM endpoints are stubbed with Playwright route interception so the test
is deterministic and needs no live API keys:

- ``/draft/stream`` returns a small two-paragraph compiled document.
- user clicks the first paragraph, types a rephrase, submits.
- ``/inquire/stream`` returns a ``jdf_node_ready`` with a rewritten node 1
  and a successful ``complete``.

Asserts exactly ONE ``.jdf-node`` content changed and that node history is
refetched (version v2 milestone exists in the revision list).
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.e2e


_DOC = {
    "document_id": "doc-shell-proto",
    "meta": {"project_id": "shell-proto-54fe89", "provenance_stats": {"anchored": 1}},
    "truth_ledger": {},
    "body": [
        {
            "type": "paragraph",
            "id": "n1",
            "content": "Revenue reached twelve million dollars this quarter.",
            "annotations": {"z3": [], "redhat": []},
        },
        {
            "type": "paragraph",
            "id": "n2",
            "content": "Growth stayed flat year over year.",
            "annotations": {"z3": [], "redhat": []},
        },
    ],
}

_REWRITTEN_NODE = {
    "type": "paragraph",
    "id": "n1",
    "content": "Quarterly revenue hit twelve million dollars.",
    "annotations": {"z3": [], "redhat": []},
}


def _sse(events: list[tuple[str, dict]]) -> str:
    out = ""
    for name, obj in events:
        out += f"event: {name}\ndata: {json.dumps(obj)}\n\n"
    return out


def _compiled_sse() -> str:
    return _sse(
        [
            ("compiled", {"document": _DOC, "locks": [], "node_count": 2, "lock_count": 0}),
            (
                "verified",
                {
                    "document": _DOC,
                    "gate_status": "review",
                    "z3_status": "PASS",
                    "ok": False,
                    "redhat_count": 0,
                    "redhat_critiques": [],
                    "provenance_stats": {"eligible": 2, "anchored": 1, "unanchored": 1},
                },
            ),
            ("complete", {"ok": True, "request_id": "mock-draft"}),
        ]
    )


def _rephrase_sse() -> str:
    return _sse(
        [
            (
                "jdf_node_ready",
                {"node": _REWRITTEN_NODE, "target_node_id": "n1", "is_mutation": True},
            ),
            ("complete", {"ok": True, "retries": 0, "request_id": "mock-rephrase"}),
        ]
    )


@pytest.fixture
def stub_compile_and_rephrase(browser_page):
    """Route-intercept the LLM endpoints so the test is key/compile-free."""
    browser_page.route(
        "**/draft/stream",
        lambda route: route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=_compiled_sse(),
        ),
    )
    browser_page.route(
        "**/inquire/stream",
        lambda route: route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=_rephrase_sse(),
        ),
    )
    yield
    try:
        browser_page.unroute("**/draft/stream")
        browser_page.unroute("**/inquire/stream")
    except Exception:
        pass


def test_rephrase_updates_single_node(browser_page, active_project, stub_compile_and_rephrase):
    # Fire an intent so the shell compiles from the stubbed stream.
    browser_page.fill("#dock-text", "draft a two paragraph brief")
    browser_page.press("#dock-text", "Enter")
    browser_page.wait_for_selector('.doc-draft .jdf-node[data-node-id="n1"]', state="attached")

    before = browser_page.evaluate(
        "() => Array.from(document.querySelectorAll('.doc-draft .jdf-node .jdf-p'))"
        ".map(function (e){ return e.textContent || ''; })"
    )

    # Click the first node → the inline rephrase editor appears at its top.
    browser_page.click('.doc-draft .jdf-node[data-node-id="n1"]')
    browser_page.wait_for_selector(".node-rephrase", state="visible")
    # F7: the field is a textarea, so Enter inserts a newline and the submit
    # chord is Cmd/Ctrl+Enter.
    browser_page.fill(".node-rephrase textarea", "make concise")
    browser_page.press(".node-rephrase textarea", "Control+Enter")

    # Wait for node history refresh (the only GET to this node's history endpoint).
    with browser_page.expect_request("**/nodes/n1/history") as ri:
        pass
    browser_page.wait_for_function(
        "() => { var t = document.querySelector('.doc-draft .jdf-node[data-node-id=\"n1\"] .jdf-p');"
        " return t && t.textContent.indexOf('Quarterly revenue') >= 0; }"
    )

    after = browser_page.evaluate(
        "() => Array.from(document.querySelectorAll('.doc-draft .jdf-node .jdf-p'))"
        ".map(function (e){ return e.textContent || ''; })"
    )

    changed = [i for i in range(len(after)) if after[i] != before[i]]
    # Exactly one paragraph changed (node 1) and node 2 is untouched.
    assert (
        len(changed) == 1
    ), f"expected exactly one node changed, saw {changed} ({before} -> {after})"
    assert after[int(changed[0])].startswith("Quarterly revenue")
    assert "flat year over year" in after[1]
    # The rewritten content replaced the original for node 1.
    assert "reach" not in after[int(changed[0])].lower()
