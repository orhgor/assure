"""Full-context scan API — Z3 + Red-Hat issue list."""

from __future__ import annotations

import pytest

from prompt_matrix.services.full_context_scan import run_full_context_scan
from tests.test_founder_restore import _reset_db_path


SAMPLE_SCAN_DOC = {
    "document_id": "doc-founder",
    "meta": {"project_id": "founder"},
    "truth_ledger": {"section_7_baseline": 4_000_000},
    "body": [
        {
            "type": "section",
            "id": "sec-1",
            "title": "Main",
            "children": [
                {
                    "type": "paragraph",
                    "id": "para-1",
                    "content": (
                        "The aggregate liability limit is $5,000,000 across all Boston locations."
                    ),
                    "meta": {"lock_pills": []},
                    "annotations": {
                        "redhat": [
                            {
                                "id": "rh-1",
                                "text": "Unsupported aggregate cap without endorsement review.",
                                "status": "open",
                            }
                        ],
                        "z3": [],
                    },
                    "provenance": [],
                }
            ],
        }
    ],
}


def test_run_full_context_scan_finds_ranked_issues() -> None:
    result = run_full_context_scan(SAMPLE_SCAN_DOC)
    assert result["status"] == "success"
    assert result["scanned_nodes"] == 1
    issues = result["issues"]
    assert issues
    assert issues[0]["severity"] == "high"
    categories = {row["category"] for row in issues}
    assert "Unverified Number" in categories
    assert any("1,000,000" in row["description"] for row in issues)
    assert "Red-Hat Finding" in categories
    assert all(row["id"].startswith("issue-") for row in issues)


@pytest.fixture()
def founder_client(tmp_path, monkeypatch):
    _reset_db_path(monkeypatch, tmp_path / "history.sqlite")
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    return create_app(require_auth=False).test_client()


def test_scan_route_success(founder_client) -> None:
    res = founder_client.post(
        "/api/projects/founder/scan",
        json={"document": SAMPLE_SCAN_DOC},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "success"
    assert body["issue_count"] >= 1
    assert isinstance(body["issues"], list)
    assert body["issues"][0]["severity"] in {"high", "medium", "low"}


def test_scan_route_requires_document(founder_client) -> None:
    res = founder_client.post("/api/projects/founder/scan", json={})
    assert res.status_code == 400
