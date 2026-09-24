"""Parser router integration: every ingest entrypoint routes through select_parser."""

from __future__ import annotations

import io

import pytest


@pytest.fixture
def ingest_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "router.db"))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    return monkeypatch


def test_substrate_extract_calls_router(ingest_env, monkeypatch):
    """extract_document_text asks the router which parser to run."""
    import prompt_matrix.routers.substrate as substrate_mod

    calls = []

    def fake_select(file_bytes, filename=None, *, source_kind=None):
        calls.append({"filename": filename, "source_kind": source_kind})
        return "jdf"

    monkeypatch.setattr("prompt_matrix.services.parser_router.select_parser", fake_select)
    # JDF branch on non-text bytes → bundle path; stub the converter.
    import prompt_matrix.services.jdf_converter as conv

    monkeypatch.setattr(
        conv,
        "pdf_to_parse_bundle",
        lambda *a, **k: {
            "text": "Routed text.",
            "tables": [],
            "images": [],
            "figures": [],
            "forms": [],
            "page_count": 1,
            "parser_name": "jdf-cli",
            "source_kind": "pdf",
            "parse_confidence": None,
            "ocr_confidence": None,
            "table_count": 0,
            "image_count": 0,
            "figure_count": 0,
            "asset_summary": {"tables": 0, "images": 0, "figures": 0},
        },
    )
    out = substrate_mod.extract_document_text("doc.pdf", b"%PDF-1.4 fake")
    assert calls == [{"filename": "doc.pdf", "source_kind": None}]
    assert out["parser_name"] == "jdf-cli"


def test_substrate_textract_routing_skips_jdf(ingest_env, monkeypatch):
    """A router "textract" decision must not touch the JDF bundle path."""
    import prompt_matrix.routers.substrate as substrate_mod
    import prompt_matrix.services.jdf_converter as conv

    def fail_bundle(*a, **k):
        raise AssertionError("textract routing must not call pdf_to_parse_bundle")

    monkeypatch.setattr(conv, "pdf_to_parse_bundle", fail_bundle)
    monkeypatch.setattr(
        "prompt_matrix.services.parser_router.select_parser",
        lambda *a, **k: "textract",
    )

    class FakeClient:
        def _get_page_count(self, file_bytes, filename):
            return 1

        def extract_text(self, file_bytes, filename):
            return {
                "text": "Textract scan text with plenty of characters here.",
                "tables": [],
                "forms": [],
                "page_count": 1,
            }

    monkeypatch.setattr(substrate_mod, "TextractClient", FakeClient)
    out = substrate_mod.extract_document_text("scan.pdf", b"%PDF-1.4 fake")
    assert out["text"].startswith("Textract scan text")
    assert out["parser_name"] == "textract"
def test_jdf_routes_entrypoint_calls_router(ingest_env, monkeypatch):
    """import_project_pdf routes through select_parser."""
    from prompt_matrix.routers import jdf_routes

    calls = []

    def fake_select(file_bytes, filename=None, *, source_kind=None):
        calls.append(filename)
        return "jdf"

    monkeypatch.setattr(
        "prompt_matrix.services.parser_router.select_parser", fake_select
    )
    monkeypatch.setattr(
        "prompt_matrix.services.jdf_converter.pdf_to_parse_bundle",
        lambda *a, **k: {
            "jdf": {"$jdf": "1.0", "meta": {}, "pages": []},
            "chunks": [{"id": "c0", "text": "chunk0", "tokens": 4}],
            "text": "chunk0",
            "page_count": 1,
            "parser_name": "jdf-cli",
            "source_kind": "pdf",
            "parse_confidence": None,
            "ocr_confidence": None,
            "tables": [],
            "images": [],
            "figures": [],
            "table_count": 0,
            "image_count": 0,
            "figure_count": 0,
            "asset_summary": {"tables": 0, "images": 0, "figures": 0},
            "filename": "a.pdf",
        },
    )
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    # The route validates upload bytes with the real PDF parser; this test
    # stubs the parse, so stub validation too.
    monkeypatch.setattr(
        "prompt_matrix.routers.jdf_routes.validate_upload_bytes", lambda *a, **k: None
    )
    init_db()
    client = create_app(require_auth=False).test_client()
    res = client.post(
        "/api/projects/rt1/import-pdf",
        data={"file": (io.BytesIO(b"%PDF-1.4 fake"), "a.pdf")},
        content_type="multipart/form-data",
    )
    assert res.status_code in (200, 201)
    assert calls == ["a.pdf"]
    assert res.get_json()["parser_name"] == "jdf-cli"


def test_jdf_memory_ingest_routes_through_router(ingest_env, monkeypatch):
    """The memory ingest calls select_parser; a textract decision uses the
    wrapped Textract bundle instead of the JDF CI bundle."""
    from prompt_matrix.routers import jdf_memory_routes as mem

    calls = []

    def fake_select(file_bytes, filename=None, *, source_kind=None):
        calls.append(filename)
        return "textract"

    monkeypatch.setattr(mem, "select_parser", fake_select)

    def fail_bundle(*a, **k):
        raise AssertionError("textract routing must not call pdf_to_parse_bundle")

    monkeypatch.setattr(mem, "pdf_to_parse_bundle", fail_bundle)

    class FakeClient:
        def extract_text(self, file_bytes, filename):
            return {
                "text": "Scanned policy text with enough characters here.",
                "tables": [],
                "forms": [],
                "page_count": 2,
            }

    monkeypatch.setattr(mem, "TextractClient", FakeClient)
    monkeypatch.setattr(mem, "remember_vault_file", lambda *a, **k: None)
    monkeypatch.setattr(
        mem,
        "build_omp_artifact_from_parse",
        lambda *a, **k: type("A", (), {"artifact_id": "omp-scan"})(),
    )
    monkeypatch.setattr(mem, "store_omp_artifact", lambda *a, **k: None)

    def fake_remember(doc_id, jdf_dict, chunks, tenant_id="default"):
        return {
            "doc_id": doc_id,
            "chunks_total": len(chunks),
            "chunks_stored": len(chunks),
        }

    monkeypatch.setattr(mem, "remember_jdf_document", fake_remember)

    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db()
    client = create_app(require_auth=False).test_client()
    res = client.post(
        "/api/projects/rt2/jdf/ingest",
        data={"file": (io.BytesIO(b"%PDF-1.4 fake"), "scan.pdf")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    assert calls == ["scan.pdf"]
    assert res.get_json()["parser_name"] == "textract"


def test_router_is_the_single_routing_decision():
    """No ingest helper holds its own jdf/textract branch: selection lives
    only in parser_router.py. Export-format checks (``fmt == "jdf"``) are
    export serialization, not parser routing, and are not offenders."""
    import prompt_matrix
    from pathlib import Path

    root = Path(prompt_matrix.__file__).parent
    offenders = []
    for path in (root / "routers").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "parser_router" in text:
            continue
        # A parser-routing decision compares the *backend name*; an export
        # format check compares a serialization format on a query param.
        if '== "textract"' in text:
            offenders.append(str(path))
        elif '== "jdf"' in text and ("parse" in text.lower() or "Textract" in text):
            offenders.append(str(path))
    assert not offenders, f"inline parser routing found in {offenders}"