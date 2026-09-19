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


def test_an_ampersand_is_data_and_survives_the_round_trip(tmp_path, monkeypatch) -> None:
    """Text with no markup in it is stored byte for byte.

    ``bleach.clean`` escapes every bare ``&`` even in a string with no tag in it,
    so a renewal memo's "Princeton Excess & Surplus Lines Insurance Company" was
    written to SQLite, served and exported as ``&amp;`` — a mangled company name in
    the .docx and markdown exports, and a JDF whose declared hash no longer
    matched the tree a re-import of it produced (measured on a compiled policy:
    first divergence at char 210 of the paragraph, ``&`` vs ``&amp;``).
    """
    pytest.importorskip("bleach")
    company = "Coverage issued by Princeton Excess & Surplus Lines Insurance Company"
    assert sanitize_jdf_node({"content": company})["content"] == company

    client = _client(tmp_path, monkeypatch)
    res = client.put("/api/projects/default/jdf", json={"document": _tree(company)})
    assert res.status_code == 200
    stored = res.get_json()["document"]["body"][0]["children"][0]["content"]
    assert stored == company
    assert "&amp;" not in stored
    # Reading the stored text back through the guard is what an import does, and
    # it has to be an identity or the export can never hash to the import.
    assert sanitize_jdf_node({"content": stored})["content"] == stored


def test_markup_is_stripped_and_the_text_beside_it_still_round_trips(tmp_path, monkeypatch) -> None:
    """The injection guard keeps its teeth on text that does carry markup.

    A sub-limit line ("$500,000 x/s $10,000,000"), a bare angle bracket ("premium
    < $1,000,000") and an allowed tag with an ampersand beside it all survive as
    the same text, while a script tag and an event handler do not.
    """
    pytest.importorskip("bleach")
    bounded = "Sublimit $500,000 x/s $10,000,000 per occurrence"
    assert sanitize_jdf_node({"content": bounded})["content"] == bounded
    bracket = "Premium < $1,000,000 & the deductible is 2%"
    assert sanitize_jdf_node({"content": bracket})["content"] == bracket

    dirty = '<script>alert(1)</script><img src=x onerror="alert(2)">Excess & Surplus <strong>ok</strong>'
    cleaned = sanitize_jdf_node({"content": dirty})["content"]
    assert "<script>" not in cleaned.lower()
    assert "onerror" not in cleaned.lower()
    assert "javascript:" not in cleaned.lower()
    assert "Excess & Surplus" in cleaned
    assert "<strong>ok</strong>" in cleaned
    # Idempotent: what the guard writes, the guard reads back unchanged.
    assert sanitize_jdf_node({"content": cleaned})["content"] == cleaned

    client = _client(tmp_path, monkeypatch)
    res = client.put("/api/projects/default/jdf", json={"document": _tree(dirty)})
    assert res.status_code == 200
    stored = res.get_json()["document"]["body"][0]["children"][0]["content"]
    assert "<script>" not in stored.lower()
    assert "onerror" not in stored.lower()
    assert "Excess & Surplus" in stored
    assert sanitize_jdf_node({"content": stored})["content"] == stored


def test_the_load_path_still_strips_markup(tmp_path, monkeypatch) -> None:
    """A sidecar carrying a script tag must not smuggle it into a fresh project.

    The compile writes model text to the revision without the guard, so an
    exported file can contain markup. Loading it sanitizes, exactly as the old
    guard did — the fix only stops it from escaping text that has no markup in it.
    """
    pytest.importorskip("bleach")
    from prompt_matrix.db.jdf_repository import ensure_project, save_jdf_revision

    client = _client(tmp_path, monkeypatch)
    ensure_project("sidecar-xss", "Sidecar XSS")
    save_jdf_revision(
        "sidecar-xss",
        _tree('<script>alert(1)</script>Excess & Surplus <img src=x onerror="alert(2)">'),
        mutation_type="compile",
    )
    sidecar = client.get("/api/projects/sidecar-xss/export?format=jdf").get_json()

    fresh = (client.post("/api/projects", json={"title": "Import XSS"}).get_json() or {})["id"]
    res = client.post(f"/api/projects/{fresh}/import-jdf", json=sidecar)
    assert res.status_code == 200, res.get_data(as_text=True)

    content = client.get(f"/api/projects/{fresh}/jdf").get_json()["document"]["body"][0][
        "children"
    ][0]["content"]
    assert "<script>" not in content.lower()
    assert "onerror" not in content.lower()
    assert "Excess & Surplus" in content
