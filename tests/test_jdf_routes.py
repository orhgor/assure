"""Tests for JDF memory routes (project-scoped ingest/search/health)."""
import io
import json
import sys

import pytest

from prompt_matrix.routers import jdf_memory_routes
from prompt_matrix.services.jdf_memory import OmpUnavailable


OMP_BUILD_KWARGS: list[dict] = []


@pytest.fixture
def client(monkeypatch, tmp_path):
    # Isolated DB: a successful ingest now writes this project's substrate_vault
    # row (the compile's grounding source), so the tests must not touch the
    # developer's history.sqlite.
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "routes.db"))
    import prompt_matrix.history as history_mod
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    history_mod.DB_PATH = history_mod._resolve_db_path()
    init_db()

    # The full app, so the ingest is exercised against the same /substrate route
    # the shell reads its source ids from.
    app = create_app(require_auth=False)

    monkeypatch.setattr(
        jdf_memory_routes,
        "pdf_to_parse_bundle",
        lambda b, **kw: {
            "jdf": {"$jdf": "1.0", "meta": {}, "pages": []},
            "chunks": [{"id": "c0", "text": "chunk0", "tokens": 4}],
            "text": "chunk0",
            "page_count": 1,
            "parser_name": "jdf-cli",
            "source_kind": "pdf",
            "parse_confidence": None,
            "ocr_confidence": None,
            "tables": [{"id": "t1", "text": "A|B"}],
            "images": [],
            "figures": [{"id": "f1", "text": "Fig 1"}],
            "table_count": 1,
            "image_count": 0,
            "figure_count": 1,
            "asset_summary": {"tables": 1, "images": 0, "figures": 1},
            "filename": "a.pdf",
        },
    )
    # Captured OMP build kwargs, asserted in the ingest tests below.
    OMP_BUILD_KWARGS.clear()

    def fake_build_omp_artifact(project_id, substrate_result, **kwargs):
        from types import SimpleNamespace

        OMP_BUILD_KWARGS.append({"project_id": project_id, **kwargs})
        return SimpleNamespace(artifact_id="omp-parse-fixed", payload=substrate_result)

    monkeypatch.setattr(
        jdf_memory_routes, "build_omp_artifact_from_parse", fake_build_omp_artifact
    )

    def fake_store_omp_artifact(project_id, artifact):
        return artifact.artifact_id

    monkeypatch.setattr(jdf_memory_routes, "store_omp_artifact", fake_store_omp_artifact)
    monkeypatch.setattr(
        jdf_memory_routes,
        "remember_jdf_document",
        lambda doc_id, jdf_dict, chunks, tenant_id="default": {
            "doc_id": doc_id,
            "chunks_total": len(chunks),
            "chunks_stored": len(chunks),
            "chunks_failed": 0,
            "chunks_pruned": 0,
            "partial": False,
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
    # The vault index is an OMP write; stub it so no test needs an OMP server.
    monkeypatch.setattr(jdf_memory_routes, "remember_vault_file", lambda *a, **k: None)
    return app.test_client()


def _pdf():
    return {"file": (io.BytesIO(b"%PDF-1.4 fake"), "a.pdf")}


def test_ingest_no_file(client):
    res = client.post("/api/projects/p1/jdf/ingest")
    assert res.status_code == 400


def test_ingest_wrong_ext(client):
    """The old 'only PDF in MVP' restriction is gone: a text-like file is
    accepted and wrapped as a JDF document (the router's text wrap path)."""
    res = client.post(
        "/api/projects/p1/jdf/ingest",
        data={"file": (io.BytesIO(b"x"), "a.txt")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["parser_name"] == "text"
    assert body["source_kind"] == "text"


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


def test_ingest_leaves_a_grounding_source(client):
    """A successful ingest must leave this project a substrate_vault entry.

    A compile grounds only through substrate_file_ids -> fetch_substrate_entries_by_ids
    and the source panel counts GET /substrate, so an ingest that only indexed
    chunks left the document ungrounded ("0 sources", substrate_file_ids: [],
    Red-Hat "no substrate or empty draft").
    """
    from prompt_matrix.db.substrate_repository import fetch_substrate_entries_by_ids

    res = client.post(
        "/api/projects/p1/jdf/ingest", data=_pdf(), content_type="multipart/form-data"
    )
    assert res.status_code == 200

    listed = client.get("/api/projects/p1/substrate").get_json()
    assert [f["filename"] for f in listed["files"]] == ["a.pdf"]

    file_id = listed["files"][0]["id"]
    stored = client.get(f"/api/projects/p1/substrate/{file_id}").get_json()["file"]
    assert stored["extracted_text"] == "chunk0"
    # What the shell posts back verbatim as substrate_file_ids:
    rows = fetch_substrate_entries_by_ids("p1", [file_id])
    assert [r["extracted_text"] for r in rows] == ["chunk0"]


def test_reingest_keeps_one_source_with_the_same_id(client):
    """Re-uploading the same file must not stack sources or change the id."""
    first = client.post(
        "/api/projects/p1/jdf/ingest", data=_pdf(), content_type="multipart/form-data"
    )
    assert first.status_code == 200
    first_id = client.get("/api/projects/p1/substrate").get_json()["files"][0]["id"]

    second = client.post(
        "/api/projects/p1/jdf/ingest", data=_pdf(), content_type="multipart/form-data"
    )
    assert second.status_code == 200
    files = client.get("/api/projects/p1/substrate").get_json()["files"]
    assert [f["id"] for f in files] == [first_id]


def test_ingest_response_fields_unchanged(client):
    """The shell reads chunks_stored/chunks_total/doc_id/ok/partial."""
    res = client.post(
        "/api/projects/p1/jdf/ingest", data=_pdf(), content_type="multipart/form-data"
    )
    body = res.get_json()
    assert body["ok"] is True
    assert body["doc_id"] == "a.pdf"
    assert (body["chunks_total"], body["chunks_stored"], body["partial"]) == (1, 1, False)


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

    def recall(_query, **_kwargs):
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

    def recall(_query, **_kwargs):
        return {
            "memories": [
                _chunk_memory("legacy.pdf", "hash-whatever", "legacy"),
                _chunk_memory("other.pdf", "hash-whatever", "other"),
            ]
        }

    monkeypatch.setattr(jm, "omp_recall", recall)
    assert [c["text"] for c in jm.get_doc_chunks("legacy.pdf", "t1")] == ["legacy"]


def _configured_omp(monkeypatch):
    """A deployment that was given an OMP: the OMP write accounting below applies.

    Without ``OMP_SERVER`` the durable PostgreSQL index is the index and
    remember_jdf_document reports it (``index: "postgres"``) instead of counting
    OMP writes — see test_without_omp_the_durable_index_is_the_index.
    """
    monkeypatch.setenv("OMP_SERVER", "http://omp.test")


def test_partial_omp_write_is_reported(monkeypatch, tmp_path):
    """0 < stored < attempted must be visible, not a silent success (FIX 2)."""
    jm = _temp_db(monkeypatch, tmp_path)
    _configured_omp(monkeypatch)
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
    _configured_omp(monkeypatch)
    monkeypatch.setattr(jm, "safe_omp_remember", lambda key, payload: None)

    chunks = [{"text": "one"}, {"text": "two"}]
    with pytest.raises(OmpUnavailable):
        jm.remember_jdf_document("dead.pdf", {"$jdf": "1.0"}, chunks, tenant_id="t1")


def test_full_omp_write_reports_not_partial(monkeypatch, tmp_path):
    jm = _temp_db(monkeypatch, tmp_path)
    _configured_omp(monkeypatch)
    monkeypatch.setattr(jm, "safe_omp_remember", lambda key, payload: {"id": 1})

    chunks = [{"text": "one"}, {"text": ""}]
    result = jm.remember_jdf_document("ok.pdf", {"$jdf": "1.0"}, chunks, tenant_id="t1")

    assert (result["chunks_stored"], result["chunks_failed"], result["partial"]) == (1, 0, False)


def test_without_omp_the_durable_index_is_the_index(monkeypatch, tmp_path):
    """No OMP_SERVER (local and staging containers): ingest and search run on PostgreSQL.

    Before 2026-09-23 this deployment wrote 0 chunks (safe_omp_remember returned
    None), raised OmpUnavailable on every dock ingest and answered every search
    with count 0.
    """
    jm = _temp_db(monkeypatch, tmp_path)
    monkeypatch.delenv("OMP_SERVER", raising=False)
    monkeypatch.setattr("prompt_matrix.omp_client.API_KEY_PATH", str(tmp_path / "no-key"))
    omp_writes: list = []
    monkeypatch.setattr(jm, "safe_omp_remember", lambda *a, **k: omp_writes.append(a) or None)
    monkeypatch.setattr(jm, "omp_recall", lambda *a, **k: {"memories": []})

    chunks = [{"text": "the policy liability limit is $5,000,000", "page": 3}, {"text": ""}]
    result = jm.remember_jdf_document("policy.pdf", {"$jdf": "1.0"}, chunks, tenant_id="t1")
    assert result["index"] == "postgres"
    assert (result["chunks_stored"], result["chunks_failed"], result["partial"]) == (1, 0, False)

    hits = jm.search_jdf_chunks("liability limit", "t1")
    assert [h["text"] for h in hits] == ["the policy liability limit is $5,000,000"]
    assert hits[0]["meta"]["page"] == 3 and hits[0]["doc_id"] == "policy.pdf"
    assert jm.search_jdf_chunks("liability limit", "t2") == [], "tenant scoped"
    assert jm.search_jdf_chunks("unmatched terms", "t1") == []

    # A new generation replaces the old one in the index.
    jm.remember_jdf_document(
        "policy.pdf",
        {"$jdf": "1.0", "pages": [{"text": "v2"}]},
        [{"text": "the liability limit is now $7,000,000"}],
        tenant_id="t1",
    )
    assert [h["text"] for h in jm.search_jdf_chunks("liability limit", "t1")] == [
        "the liability limit is now $7,000,000"
    ]

    forgotten = jm.forget_jdf_document("policy.pdf", tenant_id="t1")
    assert (forgotten["chunks_removed"], forgotten["documents_removed"]) == (1, 1)
    assert jm.search_jdf_chunks("liability limit", "t1") == []


def test_parse_chunk_content_accepts_cache_marker():
    """OMP content may carry a writer's cache marker (e.g. "PEM_CACHE_V1\\n{...}").

    A bare json.loads over such content raised, and search_jdf_chunks skipped
    every memory it could not parse. A marked payload must still be returned.
    """
    import prompt_matrix.services.jdf_memory as jm

    payload = {"kind": "jdf_chunk", "tenant": "t1", "doc_id": "policy.pdf", "text": "liability"}
    body = json.dumps(payload)

    assert jm._parse_chunk_content({"content": body}) == payload, "bare JSON is the fast path"
    assert jm._parse_chunk_content({"content": "PEM_CACHE_V1\n" + body}) == payload, "marked JSON"
    assert jm._parse_chunk_content({"content": payload}) == payload, "already-parsed dict"
    assert jm._parse_chunk_content({"content": "PEM_CACHE_V1\nnot json"}) is None, "no fabrication"
    assert jm._parse_chunk_content({"content": ""}) is None
    assert jm._parse_chunk_content("not a memory") is None


def test_search_finds_chunks_ranked_behind_cache_blobs(monkeypatch, tmp_path):
    """Keyword recall ranks over the whole store, so cache blobs win the window.

    Measured on the box 2026-09-17: for "liability limit" at OMP's default
    limit of 10 every slot was a 6 KB PEM_CACHE_V1 AST blob (they carry the
    ledger's liability_limit words) and search returned 0 hits with ok:true;
    at limit 50 the 22 jdf_chunks of the tenant were in the window.
    """
    jm = _temp_db(monkeypatch, tmp_path)
    blobs = [
        {
            "content": "PEM_CACHE_V1\n"
            + json.dumps({"compiled": {"document": {"document_id": "doc-shell-proto-%d" % i}}})
        }
        for i in range(40)
    ]
    chunk = _chunk_memory("policy.pdf", "hash-1", "liability limit per occurrence")

    def recall(_query, **kwargs):
        ranked = blobs + [chunk]  # chunk ranks last, exactly as live
        return {"memories": ranked[: int(kwargs.get("limit", 10))]}

    monkeypatch.setattr(jm, "omp_recall", recall)

    assert [c["text"] for c in jm.search_jdf_chunks("liability limit", "t1")] == [
        "liability limit per occurrence"
    ]


def test_search_survives_a_starved_recall_window(monkeypatch, tmp_path):
    """The tenant's own chunk must be found when the query window is full.

    Live 2026-09-17: a project that had just ingested policy-sample.pdf
    (chunks_stored:1) returned count:0 for "liability limit" — 20 duplicate
    generations of that document plus the 6 KB PEM_CACHE_V1 blobs took every
    recall slot — while "POLICY SAMPLE", which nothing else matches, returned 1.
    Retrieving the document's own key is what makes a hit independent of the
    window, so the fake recall here answers the key query only.
    """
    jm = _temp_db(monkeypatch, tmp_path)
    jm._persist_jdf_document("policy", "hash-1", {"$jdf": "1.0"}, "t1")
    chunk = _chunk_memory("policy", "hash-1", "the policy liability limit is $5,000,000")
    window = [
        {
            "content": "PEM_CACHE_V1\n"
            + json.dumps({"compiled": {"document": {"document_id": "doc-shell-proto-%d" % i}}})
        }
        for i in range(60)
    ]

    def recall(query, **_kwargs):
        if query == "liability limit":  # the user's query: competitors only
            return {"memories": window}
        return {"memories": [chunk]}  # a document key selector

    monkeypatch.setattr(jm, "omp_recall", recall)

    assert [c["text"] for c in jm.search_jdf_chunks("liability limit", "t1")] == [
        "the policy liability limit is $5,000,000"
    ]
    # The local ranking still drops chunks without any query token.
    assert jm.search_jdf_chunks("unmatched terms", "t1") == []


def test_search_dedupes_a_repeated_generation(monkeypatch, tmp_path):
    """One hit per chunk, even when the store holds the row twice."""
    jm = _temp_db(monkeypatch, tmp_path)
    duplicated = _chunk_memory("legacy.pdf", "hash-1", "liability limit per occurrence")
    monkeypatch.setattr(
        jm, "omp_recall", lambda *a, **k: {"memories": [duplicated, duplicated]}
    )

    assert len(jm.search_jdf_chunks("liability limit", "t1")) == 1


def test_doc_hash_ignores_the_producer_title():
    """jdf-cli titles a JDF from the temp file it converted.

    pdf_to_jdf() converts a temp copy, so the same bytes were hashed as a new
    generation on every ingest (tenant default ended up with 20 identical
    "liability limit" hits, each with its own doc_hash).
    """
    import prompt_matrix.services.jdf_memory as jm

    pages = [
        {"id": "page-1", "elements": [{"type": "text", "content": "liability limit"}]}
    ]
    first = {"$jdf": "1.0.0", "meta": {"title": "tmpAAA111", "unit": "mm"}, "pages": pages}
    second = {"$jdf": "1.0.0", "meta": {"title": "tmpBBB222", "unit": "mm"}, "pages": pages}
    other = {
        "$jdf": "1.0.0",
        "meta": {"title": "tmpAAA111", "unit": "mm"},
        "pages": [{"id": "page-1", "elements": [{"type": "text", "content": "other"}]}],
    }

    assert jm._doc_hash(first) == jm._doc_hash(second)
    assert jm._doc_hash(first) != jm._doc_hash(other)


class _FakeOmpStore:
    """Rows store with omp-server's semantics: list by namespace/offset, delete by id."""

    def __init__(self):
        self.rows: list[dict] = []

    def remember(self, key, payload):
        self.rows.append(
            {
                "id": f"mem_{len(self.rows) + 1}",
                "content": payload if isinstance(payload, str) else json.dumps(payload),
                "tags": [key],
                "namespace": "project:prompt-matrix",
            }
        )
        return {"id": self.rows[-1]["id"]}

    def list_memories(self, tags=None, *, limit=20, offset=0, namespace=None):
        rows = [r for r in self.rows if not namespace or r["namespace"] == namespace]
        return {"memories": rows[offset : offset + limit], "total": len(rows)}

    def delete_memory(self, memory_id):
        before = len(self.rows)
        self.rows = [r for r in self.rows if r["id"] != memory_id]
        return len(self.rows) < before


def _fake_omp(monkeypatch, jm):
    _configured_omp(monkeypatch)
    store = _FakeOmpStore()
    monkeypatch.setattr(jm, "safe_omp_remember", store.remember)
    monkeypatch.setattr(jm, "omp_list_memories", store.list_memories)
    monkeypatch.setattr(jm, "omp_delete_memory", store.delete_memory)
    return store


def test_reingest_leaves_one_generation(monkeypatch, tmp_path):
    """Two ingests of the same content: one doc_hash, one live generation.

    The hash changed with the temp filename and the earlier chunks stayed in the
    store, so a re-ingest appended a second generation instead of replacing it.
    """
    jm = _temp_db(monkeypatch, tmp_path)
    store = _fake_omp(monkeypatch, jm)
    chunks = [{"text": "the policy liability limit is $5,000,000"}]

    first = jm.remember_jdf_document(
        "policy.pdf", {"$jdf": "1.0.0", "meta": {"title": "tmpAAA111"}}, chunks, tenant_id="t1"
    )
    second = jm.remember_jdf_document(
        "policy.pdf", {"$jdf": "1.0.0", "meta": {"title": "tmpBBB222"}}, chunks, tenant_id="t1"
    )

    assert first["doc_hash"] == second["doc_hash"]
    assert (first["chunks_pruned"], second["chunks_pruned"]) == (0, 1)
    assert len(store.rows) == 1
    assert store.rows[0]["tags"] == ["jdf:t1:policy.pdf:0"]
    assert json.loads(store.rows[0]["content"])["doc_hash"] == first["doc_hash"]


def test_prune_spares_other_tenants_and_documents(monkeypatch, tmp_path):
    """The prune deletes on payload identity, never on a shared key prefix."""
    jm = _temp_db(monkeypatch, tmp_path)
    store = _fake_omp(monkeypatch, jm)
    chunks = [{"text": "liability limit"}]

    jm.remember_jdf_document("policy.pdf", {"$jdf": "1.0.0"}, chunks, tenant_id="t2")
    jm.remember_jdf_document("other.pdf", {"$jdf": "1.0.0"}, chunks, tenant_id="t1")
    jm.remember_jdf_document("policy.pdf", {"$jdf": "1.0.0"}, chunks, tenant_id="t1")

    assert len(store.rows) == 3


# ---------------------------------------------------------------------------
# Compile → persisted revision: the tree stored for a compile must be the AUDITED
# document (the one streamed in `verified`), because only that one carries
# meta.confidenceSpans. Regression: the compile save used to persist the
# pre-audit document, so GET /jdf returned a tree with no spans and the
# confidence overlay could not be restored after a reload.
# ---------------------------------------------------------------------------


def test_compile_persists_audited_confidence_spans(monkeypatch, tmp_path):
    from tests.test_draft import _FakeGovernor
    from tests.test_founder_restore import _reset_db_path

    _reset_db_path(monkeypatch, tmp_path / "compile_spans.sqlite")
    monkeypatch.setenv("PEM_OMP_CACHE", "0")
    monkeypatch.setenv("ASSURE_JDF_DIR", str(tmp_path / "jdf"))

    from prompt_matrix.db.connection import init_db
    from prompt_matrix.db.jdf_repository import current_document_version
    from prompt_matrix.routers import draft as draft_mod
    from prompt_matrix.web import create_app

    init_db()
    client = create_app(require_auth=False).test_client()

    # A compile is grounded or not persisted (services/compile_guard): the
    # fixture carries one source and a draft that quotes it.
    source = "The policy liability limit is set at $5,000,000 for combined single limit."

    def fake_stream(_gov, _messages, *, target_ai=None, cancel_check=None):
        yield (source, 10, 5, "test/draft-model")

    def fake_locks(_text):
        return [
            {"canonical_key": "Revenue", "value": 100, "metric": "Revenue", "confidence": 0.9}
        ], "test/lock-model"

    def stub_check(_claim, _source, *, project_id=""):
        return {
            "verdict": "yes",
            "reasoning": "The source states it.",
            "model": "test/entailment-model",
            "checked_at": "2026-09-18T00:00:00+00:00",
        }

    monkeypatch.setattr(draft_mod, "_stream_model", fake_stream)
    monkeypatch.setattr(draft_mod, "run_lock_inference", fake_locks)
    monkeypatch.setattr(draft_mod, "check_entailment", stub_check)
    monkeypatch.setattr(
        draft_mod,
        "fetch_substrate_entries_by_ids",
        lambda _pid, _ids: [{"id": "sub-1", "filename": "policy.pdf", "extracted_text": source}],
    )

    project_id = "compile-spans"
    version_before = current_document_version(project_id)

    events: list[dict] = []
    for frame in draft_mod.run_draft_pipeline(
        project_id,
        intent="Restate the liability limit.",
        substrate_file_ids=["sub-1"],
        governor=_FakeGovernor(),
    ):
        for line in frame.strip().split("\n"):
            if line.startswith("data: ") and line[6:].strip() != "[DONE]":
                events.append(json.loads(line[6:]))

    verified = next(e for e in events if e.get("type") == "verified")
    streamed = verified["document"]
    streamed_doc_spans = streamed["meta"]["confidenceSpans"]
    streamed_node_spans = {
        child["id"]: child["meta"]["confidenceSpans"]
        for section in streamed["body"]
        for child in section.get("children") or []
        if (child.get("meta") or {}).get("confidenceSpans")
    }
    assert streamed_doc_spans, "fixture must produce document-level spans"
    assert streamed_node_spans, "fixture must produce node-level spans"

    # One compile = exactly one revision.
    assert current_document_version(project_id) == version_before + 1

    res = client.get(f"/api/projects/{project_id}/jdf")
    assert res.status_code == 200
    persisted = res.get_json()["document"]
    assert persisted["meta"]["confidenceSpans"] == streamed_doc_spans
    persisted_node_spans = {
        child["id"]: child["meta"]["confidenceSpans"]
        for section in persisted["body"]
        for child in section.get("children") or []
        if (child.get("meta") or {}).get("confidenceSpans")
    }
    assert persisted_node_spans == streamed_node_spans
def test_ingest_preserves_parse_metadata_and_omp_linkage(client):
    """Parse metadata and structured payload survive the ingest end to end.

    JDF CI is the default PDF parser: the route stages a parse artifact into
    OMP immediately after parse, persists parse metadata on the vault row, and
    returns it — the row, not just the response, is the record.
    """
    OMP_BUILD_KWARGS.clear()
    res = client.post(
        "/api/projects/p1/jdf/ingest", data=_pdf(), content_type="multipart/form-data"
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["parser_name"] == "jdf-cli"
    assert body["source_kind"] == "pdf"
    assert body["page_count"] == 1
    assert body["omp_artifact_id"] == "omp-parse-fixed"
    assert body["table_count"] == 1
    assert body["figure_count"] == 1

    # OMP artifact built with the full explicit parse payload
    call = OMP_BUILD_KWARGS[-1]
    assert call["parser_name"] == "jdf-cli"
    assert call["source_kind"] == "pdf"
    assert call["page_count"] == 1
    assert call["table_count"] == 1
    assert call["figure_count"] == 1
    assert call["asset_summary"] == {"tables": 1, "images": 0, "figures": 1}

    # The vault row records parse metadata + OMP linkage
    from prompt_matrix.db.substrate_repository import list_substrate_for_project

    rows = list_substrate_for_project("p1")
    assert len(rows) == 1
    row = rows[0]
    assert row["parser_name"] == "jdf-cli"
    assert row["source_kind"] == "pdf"
    assert row["table_count"] == 1
    assert row["omp_artifact_id"] == "omp-parse-fixed"


def test_ingest_confidence_passes_through(client, monkeypatch):
    """parse/OCR confidence from the bundle reach the response and the row.

    A reported 0.0 is real data — it must survive as 0.0, not be dropped by a
    truthiness guard somewhere along the pipeline.
    """
    OMP_BUILD_KWARGS.clear()

    def bundle_with_confidence(b, **kw):
        return {
            "jdf": {"$jdf": "1.0", "meta": {}, "pages": []},
            "chunks": [{"id": "c0", "text": "chunk0", "tokens": 4}],
            "text": "chunk0",
            "page_count": 1,
            "parser_name": "jdf-cli",
            "source_kind": "pdf",
            "parse_confidence": 0.73,
            "ocr_confidence": 0.0,
            "tables": [],
            "images": [],
            "figures": [],
            "table_count": 0,
            "image_count": 0,
            "figure_count": 0,
            "asset_summary": {"tables": 0, "images": 0, "figures": 0},
            "filename": "a.pdf",
        }

    monkeypatch.setattr(jdf_memory_routes, "pdf_to_parse_bundle", bundle_with_confidence)
    res = client.post(
        "/api/projects/p1/jdf/ingest", data=_pdf(), content_type="multipart/form-data"
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["parse_confidence"] == 0.73
    assert body["ocr_confidence"] == 0.0
    call = OMP_BUILD_KWARGS[-1]
    assert call["parse_confidence"] == 0.73
    assert call["ocr_confidence"] == 0.0

    from prompt_matrix.db.substrate_repository import list_substrate_for_project

    row = list_substrate_for_project("p1")[0]
    assert row["parse_confidence"] == 0.73
    assert row["ocr_confidence"] == 0.0


def test_text_file_accepted_and_parsed(client):
    """Text-like files should be accepted and wrapped as JDF.

    The router routes a text-like file to the JDF path as a wrap signal: the
    content is already the document, so no binary parse runs and the bundle
    carries parser_name "text" / source_kind "text" through to the response.
    """
    OMP_BUILD_KWARGS.clear()
    data = {"file": (io.BytesIO(b"# Test Document\n\nThis is a test document."), "test.md")}
    res = client.post(
        "/api/projects/p1/jdf/ingest", data=data, content_type="multipart/form-data"
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["parser_name"] == "text"
    assert body["source_kind"] == "text"
    assert body["parse_confidence"] == 1.0
    # The vault row records the text parse the same way a PDF parse is recorded.
    from prompt_matrix.db.substrate_repository import list_substrate_for_project

    row = list_substrate_for_project("p1")[0]
    assert row["parser_name"] == "text"
    assert row["source_kind"] == "text"


def test_json_file_accepted_and_parsed(client):
    """JSON files are text-like: accepted and wrapped as JDF."""
    OMP_BUILD_KWARGS.clear()
    data = {"file": (io.BytesIO(b'{"key": "value"}'), "data.json")}
    res = client.post(
        "/api/projects/p1/jdf/ingest", data=data, content_type="multipart/form-data"
    )
    assert res.status_code == 200
    assert res.get_json()["ok"] is True


def test_pdf_ingest_no_longer_returns_500(client, monkeypatch):
    """PDF ingest should never return the generic 500.

    An unexpected exception inside the parse block is the client's bad input,
    not a server crash: the route answers 422 with a safe message and logs the
    real cause server-side — no library names, no traceback, no raw exception
    text in the response.
    """
    data = {"file": (io.BytesIO(b"%PDF-1.4 fake"), "test.pdf")}

    def boom(b, **kw):
        raise KeyError("unexpected error")

    monkeypatch.setattr(jdf_memory_routes, "pdf_to_parse_bundle", boom)
    res = client.post(
        "/api/projects/p1/jdf/ingest", data=data, content_type="multipart/form-data"
    )
    assert res.status_code == 422
    error = res.get_json().get("error", "")
    assert "unexpected error" not in error.lower()
    assert "something went wrong" not in error.lower()
    assert "traceback" not in error.lower()
    assert "keyerror" not in error.lower()


def test_non_supported_binary_rejected(client, monkeypatch):
    """Binary files that aren't PDF should be rejected without a 500.

    The router defaults unknown binaries to the JDF path, the content probe
    says not text-like, and the real converter then refuses the bytes with
    JdfConversionError — modeled here explicitly, so the test doesn't depend
    on the jdf binary being installed.
    """
    from prompt_matrix.services.jdf_converter import JdfConversionError

    def reject(b, **kw):
        raise JdfConversionError("jdf convert failed: not a pdf")

    monkeypatch.setattr(jdf_memory_routes, "pdf_to_parse_bundle", reject)
    data = {"file": (io.BytesIO(b"\x00\x01\x02\x03\x04\x05"), "data.bin")}
    res = client.post(
        "/api/projects/p1/jdf/ingest", data=data, content_type="multipart/form-data"
    )
    assert res.status_code in (400, 422)
    error = res.get_json().get("error", "")
    assert "something went wrong" not in error.lower()
    assert "jdf convert failed" not in error.lower()
