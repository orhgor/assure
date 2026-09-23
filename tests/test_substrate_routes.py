"""Substrate ingest: structured assets, OMP staging, and confidence pass-through.

The P0 contract: PDFs go through the JDF CI bundle, tables/images/figures stay
first-class, parse output is staged into OMP immediately after the row write,
and confidence rides through verbatim — unknown stays None, a reported 0.0
survives. Fallback (Docling/Textract) remains best-effort.
"""

from __future__ import annotations

import pytest


def _reset_db_path(monkeypatch, db_path) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()


@pytest.fixture()
def ingest_env(tmp_path, monkeypatch):
    _reset_db_path(monkeypatch, tmp_path / "substrate.db")
    from prompt_matrix.db.connection import init_db

    init_db()
    # ingest_substrate_file validates the upload bytes with the real PDF
    # parser; these tests stub extraction, so stub validation too.
    monkeypatch.setattr(
        "prompt_matrix.routers.substrate.validate_upload_bytes", lambda *a, **k: None
    )
    return monkeypatch


def _extracted(**overrides):
    base = {
        "text": "Policy liability limit is $5,000,000 for combined single limit.",
        "tables": [{"id": "t1", "text": "Limit|Value"}],
        "images": [{"id": "i1", "src": ""}],
        "figures": [{"id": "f1", "text": "Chart"}],
        "forms": [],
        "page_count": 3,
        "parser_name": "jdf-cli",
        "source_kind": "pdf",
        "parse_confidence": 0.88,
        "ocr_confidence": 0.0,
        "table_count": 1,
        "image_count": 1,
        "figure_count": 1,
        "asset_summary": {"tables": 1, "images": 1, "figures": 1},
    }
    base.update(overrides)
    return base


def _stub_omp(monkeypatch, build=None, store=None):
    monkeypatch.setattr(
        "prompt_matrix.routers.substrate.build_omp_artifact_from_parse",
        build
        or (lambda *a, **k: type("A", (), {"artifact_id": "omp-x"})()),
    )
    monkeypatch.setattr(
        "prompt_matrix.routers.substrate.store_omp_artifact",
        store or (lambda *a, **k: None),
    )


def test_pdf_ingest_preserves_structured_assets(ingest_env, monkeypatch):
    from prompt_matrix.db.substrate_repository import list_substrate_for_project
    from prompt_matrix.routers.substrate import ingest_substrate_file

    _stub_extract(monkeypatch)
    _stub_omp(monkeypatch)

    result = ingest_substrate_file("proj-1", "policy.pdf", b"%PDF-1.4 fake")
    assert result["ok"] is True
    # tables/images/figures are distinct fields, never collapsed into text
    assert result["tables"] == [{"id": "t1", "text": "Limit|Value"}]
    assert result["images"] == [{"id": "i1", "src": ""}]
    assert result["figures"] == [{"id": "f1", "text": "Chart"}]
    assert result["table_count"] == 1
    assert result["image_count"] == 1
    assert result["figure_count"] == 1
    assert result["asset_summary"] == {"tables": 1, "images": 1, "figures": 1}
    assert result["page_count"] == 3
    assert result["parser_name"] == "jdf-cli"
    assert result["source_kind"] == "pdf"
    assert result["size_bytes"] == len(b"%PDF-1.4 fake")

    row = list_substrate_for_project("proj-1")[0]
    assert row["table_count"] == 1
    assert row["image_count"] == 1
    assert row["figure_count"] == 1
    assert row["asset_summary"] == {"tables": 1, "images": 1, "figures": 1}
    assert row["parser_name"] == "jdf-cli"


def test_ingest_stages_omp_artifact(ingest_env, monkeypatch):
    from prompt_matrix.db.substrate_repository import list_substrate_for_project
    from prompt_matrix.routers.substrate import ingest_substrate_file

    calls = []

    def fake_build(project_id, substrate_result, **kwargs):
        from types import SimpleNamespace

        calls.append({"project_id": project_id, **kwargs})
        return SimpleNamespace(artifact_id="omp-parse-42", payload=substrate_result)

    def fake_store(project_id, artifact):
        assert artifact.artifact_id == "omp-parse-42"
        return artifact.artifact_id

    monkeypatch.setattr(
        "prompt_matrix.routers.substrate.build_omp_artifact_from_parse", fake_build
    )
    monkeypatch.setattr(
        "prompt_matrix.routers.substrate.store_omp_artifact", fake_store
    )
    _stub_extract(monkeypatch)

    result = ingest_substrate_file("proj-omp", "policy.pdf", b"%PDF-1.4 fake")
    assert result["omp_artifact_id"] == "omp-parse-42"
    call = calls[-1]
    assert call["parse_confidence"] == 0.88
    assert call["ocr_confidence"] == 0.0  # 0.0 survives — not dropped
    assert call["parser_name"] == "jdf-cli"
    assert call["page_count"] == 3
    row = list_substrate_for_project("proj-omp")[0]
    assert row["omp_artifact_id"] == "omp-parse-42"


def test_ingest_zero_confidence_not_dropped(ingest_env, monkeypatch):
    """parse_confidence 0.0 reaches the row and the return dict as 0.0."""
    from prompt_matrix.db.substrate_repository import list_substrate_for_project
    from prompt_matrix.routers.substrate import ingest_substrate_file

    _stub_extract(monkeypatch, parse_confidence=0.0)
    _stub_omp(monkeypatch)

    result = ingest_substrate_file("proj-zero", "policy.pdf", b"%PDF-1.4 fake")
    assert result["parse_confidence"] == 0.0
    row = list_substrate_for_project("proj-zero")[0]
    assert row["parse_confidence"] == 0.0


def test_unknown_confidence_stays_none(ingest_env, monkeypatch):
    """Unknown confidence stays None in the row and the response."""
    from prompt_matrix.db.substrate_repository import list_substrate_for_project
    from prompt_matrix.routers.substrate import ingest_substrate_file

    _stub_extract(monkeypatch, parse_confidence=None, ocr_confidence=None)
    _stub_omp(monkeypatch)

    result = ingest_substrate_file("proj-none", "policy.pdf", b"%PDF-1.4 fake")
    assert result["parse_confidence"] is None
    assert result["ocr_confidence"] is None
    row = list_substrate_for_project("proj-none")[0]
    assert row["parse_confidence"] is None
    assert row["ocr_confidence"] is None


def _stub_extract(monkeypatch, **overrides):
    monkeypatch.setattr(
        "prompt_matrix.routers.substrate.extract_document_text",
        lambda filename, file_bytes: _extracted(**overrides),
)


def test_extract_document_text_prefers_jdf_bundle(ingest_env, monkeypatch):
    """PDFs go through pdf_to_parse_bundle first — JDF CI is the default."""
    import prompt_matrix.routers.substrate as substrate_mod
    import prompt_matrix.services.jdf_converter as conv

    called = []

    def fake_bundle(pdf_bytes, *, filename=None, source_kind="pdf"):
        called.append(True)
        return _extracted()

    original_bundle = conv.pdf_to_parse_bundle
    conv.pdf_to_parse_bundle = fake_bundle
    try:
        out = substrate_mod.extract_document_text("policy.pdf", b"%PDF-1.4 fake")
        assert called == [True]
        assert out["parser_name"] == "jdf-cli"
        assert out["tables"] == [{"id": "t1", "text": "Limit|Value"}]
        assert out["images"] == [{"id": "i1", "src": ""}]
        assert out["figures"] == [{"id": "f1", "text": "Chart"}]
        assert out["page_count"] == 3
        assert out["asset_summary"] == {"tables": 1, "images": 1, "figures": 1}
        assert out["parse_confidence"] == 0.88
        assert out["ocr_confidence"] == 0.0
    finally:
        conv.pdf_to_parse_bundle = original_bundle


def test_extract_document_text_falls_back_best_effort(ingest_env, monkeypatch, caplog):
    """A JDF CI failure falls back to Textract — best-effort, logged clearly."""
    import prompt_matrix.routers.substrate as substrate_mod
    import prompt_matrix.services.jdf_converter as conv

    class FakeClient:
        def _get_page_count(self, file_bytes, filename):
            return 1

        def extract_text(self, file_bytes, filename):
            return {
                "text": "Textract fallback text with plenty of characters here.",
                "tables": [],
                "forms": [],
                "page_count": 1,
            }

    monkeypatch.setattr(substrate_mod, "TextractClient", FakeClient)

    def boom(*a, **k):
        raise conv.JdfConversionError("jdf convert failed: boom")

    original_bundle = conv.pdf_to_parse_bundle
    conv.pdf_to_parse_bundle = boom
    try:
        with caplog.at_level("WARNING", logger="prompt_matrix.routers.substrate"):
            out = substrate_mod.extract_document_text("policy.pdf", b"%PDF-1.4 fake")
        assert out["text"].startswith("Textract fallback")
        # Fallback metadata: parser marked, confidence unknown stays None
        assert out["parser_name"] == "textract"
        assert out["parse_confidence"] is None
        assert any("falling back" in r.getMessage() for r in caplog.records)
    finally:
        conv.pdf_to_parse_bundle = original_bundle