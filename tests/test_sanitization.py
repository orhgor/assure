"""JDF HTML sanitization."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from prompt_matrix.lib.sanitize import sanitize_jdf_node


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "san.sqlite"))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    return create_app(require_auth=False).test_client()


def _tree(content: str) -> dict:
    from prompt_matrix.models.jdf import empty_annotations

    return {
        "document_id": "doc-xss",
        "meta": {"title": "xss"},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "s1",
                "title": "S",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "p1",
                        "content": content,
                        "entities_referenced": [],
                        "provenance": [],
                        "meta": {},
                        "annotations": empty_annotations(),
                    }
                ],
                "meta": {},
                "annotations": empty_annotations(),
            }
        ],
    }


def test_safe_content_preserved() -> None:
    node = {
        "type": "paragraph",
        "content": '<p>See <a href="https://example.com">link</a> and <strong>bold</strong></p><ul><li>one</li></ul>',
    }
    cleaned = sanitize_jdf_node(node)
    assert "link" in cleaned["content"]
    assert "bold" in cleaned["content"]
    assert "<ul>" in cleaned["content"]
    assert "<script>" not in cleaned["content"]


def test_xss_injection_blocked(tmp_path, monkeypatch) -> None:
    pytest.importorskip("bleach")
    dirty = '<script>alert(1)</script><img src=x onerror="alert(2)">safe <strong>ok</strong>'
    cleaned = sanitize_jdf_node({"content": dirty})
    assert "<script>" not in cleaned["content"].lower()
    assert "onerror" not in cleaned["content"].lower()
    assert "ok" in cleaned["content"]

    client = _client(tmp_path, monkeypatch)
    res = client.put("/api/projects/default/jdf", json={"document": _tree(dirty)})
    assert res.status_code == 200
    stored = res.get_json()["document"]["body"][0]["children"][0]["content"]
    assert "<script>" not in stored.lower()
    assert "onerror" not in stored.lower()

    from prompt_matrix.services.pdf_import import pdf_bytes_to_jdf

    def _fake_pdf(*_a, **_k):
        return _tree(dirty)

    monkeypatch.setattr("prompt_matrix.routers.jdf_routes.pdf_bytes_to_jdf", _fake_pdf)
    monkeypatch.setattr(
        "prompt_matrix.routers.jdf_routes.validate_upload_bytes",
        lambda *_a, **_k: {"filename": "x.pdf", "size_bytes": 10, "page_count": 1},
    )
    upload = client.post(
        "/api/projects/default/import-pdf",
        data={"file": (BytesIO(b"%PDF-1.4 stub"), "doc.pdf")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    text = str(upload.get_json().get("document") or "")
    assert "<script>" not in text.lower()
    assert "onerror" not in text.lower()

    from prompt_matrix.services.refine_node import apply_refined_text

    applied = apply_refined_text(_tree("hello"), "p1", dirty)
    node_text = str(applied["node"].get("content") or "")
    # apply_refined_text writes text as content; sanitize happens on persist path.
    from prompt_matrix.lib.sanitize import sanitize_jdf_node as san

    assert "<script>" not in san(node_text).lower()
