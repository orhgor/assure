"""Tests for JDF memory routes (project-scoped ingest/search/health)."""
import io
import json
import sys

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


def test_health_ok_when_jdf_binary_resolves(client, monkeypatch):
    """200 + ok:true when JDF_BIN points at a binary that exists on disk."""
    monkeypatch.setattr(jdf_memory_routes, "JDF_BIN", sys.executable)
    res = client.get("/api/projects/p1/jdf/health")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True, "status 200 must not carry ok:false"
    assert body["jdf_bin"] == sys.executable
    assert body["project_id"] == "p1"


def test_health_503_when_jdf_binary_unresolvable(client, monkeypatch):
    """503 + ok:false when neither JDF_BIN nor PATH yields a jdf binary."""
    monkeypatch.setattr(jdf_memory_routes, "JDF_BIN", "/nonexistent/bin/jdf")
    monkeypatch.setattr(jdf_memory_routes.shutil, "which", lambda name: None)
    res = client.get("/api/projects/p1/jdf/health")
    assert res.status_code == 503
    body = res.get_json()
    assert body["ok"] is False, "status 503 must not carry ok:true"
    assert body["jdf_bin"] == "/nonexistent/bin/jdf"
    assert body["project_id"] == "p1"


def test_health_ok_when_path_fallback_resolves_jdf_binary(client, monkeypatch):
    """A configured JDF_BIN that is stale (gone) still resolves via PATH."""
    monkeypatch.setattr(jdf_memory_routes, "JDF_BIN", "/nonexistent/bin/jdf")
    monkeypatch.setattr(jdf_memory_routes.shutil, "which", lambda name: "/usr/bin/jdf")
    res = client.get("/api/projects/p1/jdf/health")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["jdf_bin"] == "/nonexistent/bin/jdf"


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


def test_duplicate_doc_id_across_tenants(monkeypatch, tmp_path):
    """PK is (tenant_id, doc_id), not doc_id alone.

    Under the old schema the second tenant's ingest of the same filename
    overwrote the first tenant's durable JDF row (audit 2026-09-17 §B).
    """
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "jdf-cli.db"))
    import prompt_matrix.history as history_mod
    from prompt_matrix.db.connection import init_db

    history_mod.DB_PATH = history_mod._resolve_db_path()
    init_db()

    import prompt_matrix.services.jdf_memory as jm

    jm._persist_jdf_document("policy.pdf", "hash-t1", {"$jdf": "1.0", "tenant": "t1"}, "t1")
    jm._persist_jdf_document("policy.pdf", "hash-t2", {"$jdf": "1.0", "tenant": "t2"}, "t2")

    db = history_mod.get_db()
    count = db.execute(
        "SELECT COUNT(*) FROM jdf_cli_documents WHERE doc_id = ?", ("policy.pdf",)
    ).fetchone()[0]
    assert count == 2, "same doc_id under two tenants must be two rows"

    rows = db.execute(
        "SELECT tenant_id, jdf_json FROM jdf_cli_documents WHERE doc_id = ? ORDER BY tenant_id",
        ("policy.pdf",),
    ).fetchall()
    assert [r[0] for r in rows] == ["t1", "t2"]
    assert json.loads(rows[0][1])["tenant"] == "t1", "t1's row was overwritten by t2"

def _chunk_memory(doc_id, doc_hash, text, tenant_id="t1"):
    return {
        "content": json.dumps(
            {
                "kind": "jdf_chunk",
                "tenant": tenant_id,
                "doc_id": doc_id,
                "doc_hash": doc_hash,
                "text": text,
            }
        )
    }


def _temp_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "jdf-cli.db"))
    import prompt_matrix.history as history_mod
    from prompt_matrix.db.connection import init_db

    history_mod.DB_PATH = history_mod._resolve_db_path()
    init_db()

    import prompt_matrix.services.jdf_memory as jm

    return jm


def test_get_doc_chunks_returns_only_current_hash(monkeypatch, tmp_path):
    """Re-ingest leaves the old generation in OMP; recall must not mix them.

    Each ingest appends new rows keyed jdf:{tenant}:{doc_id}:{idx}, so the
    doc_id-only filter returned stale and fresh chunks together
    (audit 2026-09-17 §B).
    """
    jm = _temp_db(monkeypatch, tmp_path)
    jm._persist_jdf_document("policy.pdf", "hash-new", {"$jdf": "1.0"}, "t1")

    def recall(_query):
        return {
            "memories": [
                _chunk_memory("policy.pdf", "hash-old", "stale"),
                _chunk_memory("policy.pdf", "hash-new", "fresh"),
            ]
        }

    monkeypatch.setattr(jm, "omp_recall", recall)
    assert [c["text"] for c in jm.get_doc_chunks("policy.pdf", "t1")] == ["fresh"]


def test_get_doc_chunks_without_known_hash_keeps_doc_id_filter(monkeypatch, tmp_path):
    """No durable row (never ingested here) -> previous doc_id-only behaviour."""
    jm = _temp_db(monkeypatch, tmp_path)

    def recall(_query):
        return {
            "memories": [
                _chunk_memory("legacy.pdf", "hash-whatever", "legacy"),
                _chunk_memory("other.pdf", "hash-whatever", "other"),
            ]
        }

    monkeypatch.setattr(jm, "omp_recall", recall)
    assert [c["text"] for c in jm.get_doc_chunks("legacy.pdf", "t1")] == ["legacy"]


def test_partial_omp_write_is_reported(monkeypatch, tmp_path):
    """0 < stored < attempted must be visible, not a silent success (FIX 2)."""
    jm = _temp_db(monkeypatch, tmp_path)
    calls = []

    def flaky(key, payload):
        calls.append(key)
        return len(calls) == 1

    monkeypatch.setattr(jm, "safe_omp_remember", flaky)
    chunks = [{"text": "one"}, {"text": "two"}]
    result = jm.remember_jdf_document("partial.pdf", {"$jdf": "1.0"}, chunks, tenant_id="t1")

    assert result["chunks_stored"] == 1
    assert result["chunks_failed"] == 1
    assert result["partial"] is True


def test_total_omp_failure_still_raises(monkeypatch, tmp_path):
    """The 503-on-zero contract is unchanged by the partial reporting."""
    jm = _temp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(jm, "safe_omp_remember", lambda key, payload: None)

    chunks = [{"text": "one"}, {"text": "two"}]
    with pytest.raises(OmpUnavailable):
        jm.remember_jdf_document("dead.pdf", {"$jdf": "1.0"}, chunks, tenant_id="t1")


def test_full_omp_write_reports_not_partial(monkeypatch, tmp_path):
    jm = _temp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(jm, "safe_omp_remember", lambda key, payload: {"id": 1})

    chunks = [{"text": "one"}, {"text": ""}]
    result = jm.remember_jdf_document("ok.pdf", {"$jdf": "1.0"}, chunks, tenant_id="t1")

    assert (result["chunks_stored"], result["chunks_failed"], result["partial"]) == (1, 0, False)
