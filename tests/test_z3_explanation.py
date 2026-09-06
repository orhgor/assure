"""Z3 explanation strings and persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from prompt_matrix.db.jdf_repository import (
    ensure_project,
    fetch_latest_jdf_or_empty,
    save_jdf_revision,
)
from prompt_matrix.ledger.z3_ledger import check_claim
from prompt_matrix.models.jdf import empty_annotations
from prompt_matrix.services.confidence_spans import (
    attach_confidence_spans_to_document,
    build_confidence_spans,
)


def test_z3_explanation_returns_reason() -> None:
    result = check_claim(
        "Revenue is 100",
        context="CMS Bulletin p.4, section 2.1 revenue is 100",
        ledger={"revenue": 100},
        source_label="CMS Bulletin p.4, section 2.1",
    )
    assert 0.0 <= result["confidence"] <= 1.0
    assert result["reason"]
    assert "CMS Bulletin" in result["reason"]


def test_z3_explanation_matches_expected() -> None:
    hit = check_claim(
        "Revenue is 100",
        context="CMS Bulletin p.4, section 2.1 revenue is 100",
        ledger={"revenue": 100},
        source_label="CMS Bulletin p.4, section 2.1",
    )
    assert hit["status"] == "true"
    assert "Matched" in hit["reason"]
    miss = check_claim(
        "Revenue is 100",
        context="SOP has no overlapping phrase xyz",
        ledger={"revenue": 100},
        source_label="SOP",
    )
    assert (
        "No match found in SOP" in miss["reason"]
        or "no source phrase" in miss["reason"].lower()
        or "SOP" in miss["reason"]
    )


def test_z3_explanation_survives_save_reload(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "z3exp.sqlite"))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db

    init_db()
    tree = {
        "document_id": "doc-z3",
        "meta": {"title": "z3"},
        "truth_ledger": {"revenue": 100},
        "body": [
            {
                "type": "section",
                "id": "s1",
                "title": "S",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "p1",
                        "content": "Revenue is 100 according to CMS Bulletin p.4.",
                        "entities_referenced": ["revenue"],
                        "provenance": [
                            {
                                "source_type": "internal_doc",
                                "source_name": "CMS Bulletin",
                                "url_or_doi": "",
                                "source_id": "cms",
                                "page_number": "4",
                                "extracted_quote": "section 2.1",
                                "accessed_date": "",
                            }
                        ],
                        "meta": {},
                        "annotations": empty_annotations(),
                    }
                ],
                "meta": {},
                "annotations": empty_annotations(),
            }
        ],
    }
    spans = build_confidence_spans(
        tree, z3_results={"lock_results": [{"ok": True, "key": "revenue"}]}
    )
    tree = attach_confidence_spans_to_document(tree, spans)
    ensure_project("z3exp")
    save_jdf_revision("z3exp", tree, mutation_type="seed")
    loaded = fetch_latest_jdf_or_empty("z3exp")
    node = loaded["body"][0]["children"][0]
    stored = (node.get("meta") or {}).get("confidenceSpans") or loaded["meta"]["confidenceSpans"]
    assert stored
    assert any(item.get("reason") for item in stored)
    js = (
        Path(__file__).resolve().parents[1] / "prompt_matrix" / "static" / "jdf_tiptap.js"
    ).read_text(encoding="utf-8")
    assert "metaJson" in js
    assert "data-meta-json" in js
    assert "z3-reason-icon" in js
    assert "data-confidence-reason" in js
