"""Tests for JDF memory routes (project-scoped ingest/search/health)."""
import io
import json
import sys

import pytest

from prompt_matrix.routers import jdf_memory_routes
from prompt_matrix.services.jdf_memory import OmpUnavailable


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
