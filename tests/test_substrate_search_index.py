"""A source uploaded from the SOURCES panel is searchable, and forgotten on delete.

Until 2026-09-23 ``POST /api/projects/<id>/jdf/search`` found only documents
ingested through the dock (``/jdf/ingest`` → ``remember_jdf_document``). A vault
upload (``/substrate/upload`` → ``ingest_substrate_file``) reached only
``remember_vault_file``, whose rows ``_tenant_chunk_candidates`` filters out, so a
16-page PDF uploaded in SOURCES returned nothing. These tests run with no OMP
configured — the local and staging containers' situation — so the hits come from
the PostgreSQL chunk index alone.
"""

from __future__ import annotations

import io

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "search.db"))
    monkeypatch.delenv("OMP_SERVER", raising=False)
    monkeypatch.setattr("prompt_matrix.omp_client.API_KEY_PATH", str(tmp_path / "no-omp-key"))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    return create_app(require_auth=False).test_client()


SOURCE = (
    "The wind and hail deductible is twenty five thousand dollars per occurrence.\n\n"
    "Coverage territory is Suffolk County and the scheduled premises only.\n\n"
    "The renewal premium is due within thirty days of the effective date."
)


def _upload(client, project="default", filename="renewal-terms.md", text=SOURCE):
    return client.post(
        f"/api/projects/{project}/substrate/upload",
        data={"file": (io.BytesIO(text.encode("utf-8")), filename)},
        content_type="multipart/form-data",
    )


def _search(client, query, project="default"):
    res = client.post(f"/api/projects/{project}/jdf/search", json={"query": query})
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()


def test_vault_upload_is_found_by_search_and_forgotten_on_delete(client):
    up = _upload(client)
    assert up.status_code == 200, up.get_data(as_text=True)
    body = up.get_json()
    file_id = body["id"]
    # The upload reports the index it wrote: PostgreSQL, since no OMP is configured.
    assert body["search_index"]["indexed"] is True
    assert body["search_index"]["index"] == "postgres"
    assert body["search_index"]["chunks_stored"] == 3

    found = _search(client, "hail deductible")
    assert found["count"] == 1, found
    hit = found["results"][0]
    assert "twenty five thousand dollars" in hit["text"]
    # The chunk names the source it came from and the vault row the shell holds.
    assert hit["meta"]["source_filename"] == "renewal-terms.md"
    assert hit["meta"]["substrate_file_id"] == file_id
    # A .md upload has no page layer: no page number is invented for it.
    assert "page" not in hit["meta"]

    # Tenant scoped: another project does not see this project's source.
    assert _search(client, "hail deductible", project="other-project")["count"] == 0

    res = client.delete(f"/api/projects/default/substrate/{file_id}")
    assert res.status_code == 200
    assert res.get_json()["search_index"]["chunks_removed"] == 3
    assert _search(client, "hail deductible")["count"] == 0
    assert _search(client, "Suffolk County")["count"] == 0


def test_reuploading_a_changed_source_replaces_its_chunks(client):
    """The vault keys a row on (project, filename); the index follows that key.

    A second upload of the same filename is the same document in this project,
    so its earlier generation stops answering — otherwise a corrected source would
    keep returning the sentence it corrected.
    """
    _upload(client, text="The wind and hail deductible is ten thousand dollars.")
    assert _search(client, "hail deductible")["results"][0]["text"].endswith(
        "ten thousand dollars."
    )
    _upload(client, text="The wind and hail deductible is twenty thousand dollars.")
    found = _search(client, "hail deductible")
    assert [h["text"] for h in found["results"]] == [
        "The wind and hail deductible is twenty thousand dollars."
    ]


def test_search_chunks_keep_jdf_pages_and_never_invent_one():
    """The indexed chunks are jdf-cli's own when the parser produced them.

    A JDF CI parse carries chunks with their page; those travel into the index
    untouched, tagged with the source. Anything text-only is split by paragraph
    without a page number — ``page: 1`` on a multi-page Textract extraction would
    be a fabricated location.
    """
    from prompt_matrix.routers.substrate import _search_chunks_for

    jdf = {"$jdf": "1.0", "meta": {}, "pages": [{}, {}]}
    extracted = {
        "jdf": jdf,
        "chunks": [
            {"id": "c0", "text": "Page one clause.", "page": 1},
            {"id": "c1", "text": "Page two clause.", "page": 2},
        ],
        "page_count": 2,
    }
    got_jdf, chunks = _search_chunks_for(
        extracted, filename="policy.pdf", text="ignored", substrate_file_id="sub-9"
    )
    assert got_jdf is jdf
    assert [c["page"] for c in chunks] == [1, 2]
    assert all(c["substrate_file_id"] == "sub-9" for c in chunks)
    assert all(c["source_filename"] == "policy.pdf" for c in chunks)

    pseudo, paragraphs = _search_chunks_for(
        {"page_count": 16},
        filename="scan.pdf",
        text="First paragraph.\n\nSecond paragraph.",
        substrate_file_id="sub-10",
    )
    assert [c["text"] for c in paragraphs] == ["First paragraph.", "Second paragraph."]
    assert all("page" not in c for c in paragraphs)
    assert len(pseudo["pages"]) == 16
    # The pseudo-document's hash follows the text, so a changed scan is a new
    # generation and an identical one is the same generation.
    from prompt_matrix.services.jdf_memory import _doc_hash

    same, _ = _search_chunks_for(
        {"page_count": 16},
        filename="scan.pdf",
        text="First paragraph.\n\nSecond paragraph.",
        substrate_file_id="sub-10",
    )
    other, _ = _search_chunks_for(
        {"page_count": 16}, filename="scan.pdf", text="Different.", substrate_file_id="sub-10"
    )
    assert _doc_hash(pseudo) == _doc_hash(same)
    assert _doc_hash(pseudo) != _doc_hash(other)
