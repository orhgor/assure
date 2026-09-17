"""Tests for JDF memory routes (project-scoped ingest/search/health)."""
import io

import pytest
from flask import Flask

from prompt_matrix.routers import jdf_memory_routes
from prompt_matrix.routers.jdf_memory_routes import register_jdf_memory_routes
from prompt_matrix.services.jdf_memory import OmpUnavailable


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.config["TESTING"] = True
    register_jdf_memory_routes(app)

    monkeypatch.setattr(
        jdf_memory_routes,
        "pdf_to_jdf",
        lambda b: {"$jdf": "1.0", "meta": {}, "pages": []},
    )
    monkeypatch.setattr(
        jdf_memory_routes,
        "jdf_to_chunks",
        lambda d, strategy="section": [{"id": "c0", "text": "chunk0", "tokens": 4}],
    )
    monkeypatch.setattr(
        jdf_memory_routes,
        "remember_jdf_document",
        lambda doc_id, jdf_dict, chunks, tenant_id="default": {
            "doc_id": doc_id,
            "chunks_total": len(chunks),
            "chunks_stored": len(chunks),
            "doc_hash": "abc",
        },
    )
    monkeypatch.setattr(
        jdf_memory_routes,
        "search_jdf_chunks",
        lambda q, tenant_id="default", limit=20: [
            {"kind": "jdf_chunk", "tenant": tenant_id, "doc_id": "a.pdf", "text": "match"}
        ],
    )
    return app.test_client()


def _pdf():
    return {"file": (io.BytesIO(b"%PDF-1.4 fake"), "a.pdf")}


def test_ingest_no_file(client):
    res = client.post("/api/projects/p1/jdf/ingest")
    assert res.status_code == 400


def test_ingest_wrong_ext(client):
    res = client.post(
        "/api/projects/p1/jdf/ingest",
        data={"file": (io.BytesIO(b"x"), "a.txt")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 400


def test_ingest_oversized(client):
    data = b"\x00" * (26 * 1024 * 1024)
    res = client.post(
        "/api/projects/p1/jdf/ingest", data=data, content_type="application/pdf"
    )
    assert res.status_code == 413
    assert "too large" in (res.get_json() or {}).get("error", "")


def test_ingest_ok(client):
    res = client.post(
        "/api/projects/p1/jdf/ingest", data=_pdf(), content_type="multipart/form-data"
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["doc_id"] == "a.pdf"
    assert body["chunks_stored"] == 1


def test_search_empty_query(client):
    res = client.post("/api/projects/p1/jdf/search", json={"query": ""})
    assert res.status_code == 400


def test_search_ok(client):
    res = client.post("/api/projects/p1/jdf/search", json={"query": "hi"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["count"] == 1
    assert body["results"][0]["doc_id"] == "a.pdf"


def test_ingest_omp_unavailable(client, monkeypatch):
    def boom(doc_id, jdf_dict, chunks, tenant_id="default"):
        raise OmpUnavailable("down")

    monkeypatch.setattr(jdf_memory_routes, "remember_jdf_document", boom)
    res = client.post(
        "/api/projects/p1/jdf/ingest", data=_pdf(), content_type="multipart/form-data"
    )
    assert res.status_code == 503
    assert "unavailable" in res.get_json()["error"]


def test_auth_required_unauthenticated(client, monkeypatch):
    import prompt_matrix.middleware as mw

    monkeypatch.setattr(mw, "ownership_enforced", lambda: True)
    res = client.post(
        "/api/projects/p1/jdf/ingest", data=_pdf(), content_type="multipart/form-data"
    )
    assert res.status_code in (401, 403)


def test_ingest_uses_dedicated_table(monkeypatch):
    """FIX: jdf-cli docs go in jdf_cli_documents, never the existing jdf_documents
    (which holds PyMuPDF-JDF revisions via save_jdf_revision)."""
    import prompt_matrix.services.jdf_memory as jm

    sql_calls = []

    class FakeDb:
        def execute(self, sql, params=()):
            sql_calls.append(str(sql))
            return self

        def commit(self):
            pass

    fake_db = FakeDb()
    monkeypatch.setattr(jm, "init_db", lambda: None)
    monkeypatch.setattr(jm, "get_db", lambda: fake_db)

    jm._persist_jdf_document("doc1", "abc123", {"$jdf": "1.0", "pages": []}, "default")

    inserts = [s for s in sql_calls if "INSERT" in s]
    assert inserts, "no INSERT captured"
    assert "INSERT INTO jdf_cli_documents" in "\n".join(inserts)
    assert "INSERT INTO jdf_documents" not in "\n".join(inserts)