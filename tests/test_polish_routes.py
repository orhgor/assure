"""Main document polish API — grammar rewrite with lock-pill preservation."""

from __future__ import annotations

import pytest

from prompt_matrix.services.polish_document import run_polish_document
from tests.test_founder_restore import _reset_db_path


SAMPLE_DOC = {
    "document_id": "doc-founder",
    "meta": {"project_id": "founder"},
    "truth_ledger": {},
    "body": [
        {
            "type": "section",
            "id": "sec-1",
            "title": "Draft",
            "children": [
                {
                    "type": "paragraph",
                    "id": "para-1",
                    "content": "revenue reached twelve million in q3 [🔒 #01]",
                    "meta": {
                        "lock_pills": [
                            {
                                "lock_hash": "abc123hash4567",
                                "source_id": "sub-1",
                                "lock_index": 1,
                                "page_coordinates": {"page": 4},
                            }
                        ]
                    },
                    "annotations": {"redhat": [], "z3": []},
                    "provenance": [],
                }
            ],
        }
    ],
}


def test_run_polish_preserves_lock_pills() -> None:
    result = run_polish_document(SAMPLE_DOC, use_llm=False)
    assert result["strict_preservation"] is True
    assert result["lock_count"] == 1
    assert "abc123hash4567" in result["lock_hashes"]
    assert "[🔒 #01]" in result["document"]["body"][0]["children"][0]["content"]
    assert result["plain_after"] != result["plain_before"] or "Revenue" in result["plain_after"]


@pytest.fixture()
def founder_client(tmp_path, monkeypatch):
    _reset_db_path(monkeypatch, tmp_path / "history.sqlite")
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    return create_app(require_auth=False).test_client()


def test_polish_route_success(founder_client) -> None:
    res = founder_client.post(
        "/api/projects/founder/polish", json={"document": SAMPLE_DOC, "use_llm": False}
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "success"
    assert body["strict_preservation"] is True
    assert (
        body["document"]["body"][0]["children"][0]["meta"]["lock_pills"][0]["lock_hash"]
        == "abc123hash4567"
    )


def test_polish_route_requires_document(founder_client) -> None:
    res = founder_client.post("/api/projects/founder/polish", json={})
    assert res.status_code == 400
