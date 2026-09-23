"""Sprint 1 adoption engine tests."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from prompt_matrix.db.connection import init_db
from prompt_matrix.lib.logger import AuditLogger
from prompt_matrix.services.lock_inference import (
    LockInferenceResult,
    _canonical_key,
    _coerce_candidates,
    _parse_model_json,
    document_substrate_text,
    resolve_lock_inference_model,
)


def test_coerce_candidates_filters_low_confidence():
    raw = {
        "candidates": [
            {
                "entity": "Revenue",
                "metric": "ARR",
                "period": "Q1 2026",
                "value": 12_000_000,
                "unit": "USD",
                "confidence": 0.95,
            },
            {
                "entity": "Growth",
                "metric": "YoY",
                "value": 50,
                "unit": "%",
                "confidence": 0.4,
            },
        ]
    }
    out = _coerce_candidates(raw)
    assert len(out) == 1
    assert out[0]["entity"] == "Revenue"
    assert out[0]["canonical_key"] == _canonical_key(raw["candidates"][0])


def test_parse_model_json_embedded_object():
    content = 'Here is JSON:\n{"candidates":[{"entity":"Margin","metric":"Operating","value":32,"unit":"%","confidence":0.88}]}'
    out = _parse_model_json(content)
    assert len(out) == 1
    assert out[0]["value"] == 32.0


def test_parse_model_json_recovers_a_cut_off_answer():
    """A truncated answer still yields the candidates it did write.

    The measured failure this guards: a memo of coverage limits asks for ~30
    candidates, the answer is cut off mid-object at the model's output limit, and
    the whole extraction used to be dropped — the draft then reported "no locks
    inferred from the draft" and checked none of its figures, while a smaller memo
    in the same project checked its own. Complete objects are still real
    extractions; only the cut object is discarded.
    """
    content = (
        '{"candidates":['
        '{"entity":"Coverage","metric":"limit","value":5000000,"confidence":0.95},'
        '{"entity":"Coverage","metric":"attachment","value":150000000,"confidence":0.9},'
        '{"entity":"Coverage","metric":"cut off","val'
    )
    out = _parse_model_json(content)
    assert [c["value"] for c in out] == [5000000.0, 150000000.0]
    # The same acceptance rules as a clean answer: the low-confidence candidate in
    # the recovered tail is still dropped.
    mixed = (
        '{"candidates":['
        '{"entity":"Coverage","metric":"kept","value":2500,"confidence":0.95},'
        '{"entity":"Coverage","metric":"vague","value":900,"confidence":0.2},'
        '{"entity":"Coverage","metric":"cut'
    )
    assert [c["value"] for c in _parse_model_json(mixed)] == [2500.0]


def test_parse_model_json_unreadable_answer_is_not_a_ledger():
    """No candidates key, nothing invented — the failure stays a failure."""
    assert _parse_model_json("I cannot extract values from that text.") == []
    assert _parse_model_json('{"candidates": []}') == []


def test_resolve_lock_inference_model():
    assert resolve_lock_inference_model(False) == "openrouter/qwen/qwen3-next-80b-a3b-instruct"
    assert resolve_lock_inference_model(True) == "gemini/gemini-3.6-flash"


def test_pdf_has_visual_content_empty():
    from prompt_matrix.upload_limits import pdf_has_visual_content

    assert pdf_has_visual_content(b"") is False


def test_document_substrate_text():
    doc = {
        "body": [
            {
                "title": "Intro",
                "children": [{"type": "paragraph", "content": "Q3 revenue reached $4.2M."}],
            }
        ]
    }
    text = document_substrate_text(doc)
    assert "Q3 revenue" in text
    assert "Intro" in text


@pytest.fixture
def audit_db():
    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "history.sqlite"
    conn = sqlite3.connect(str(db_path))
    init_db(conn)
    conn.close()
    yield str(db_path)
    tmp.cleanup()


def test_export_audit_manifest(audit_db, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", audit_db)
    from prompt_matrix.web import create_app

    client = create_app(require_auth=False).test_client()
    # The suite runs with SQLITE_USE_POOL=0, so get_db() uses the module-level
    # DB_PATH; rebind it to this test's file the way the other DB tests do, or
    # the project row and the audit rows land in different databases.
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    # audit_log declares the FK to projects, so the project an audit row names
    # has to exist before the row can be written at all.
    from prompt_matrix.db.jdf_repository import ensure_project

    ensure_project("default", "Default project")
    audit = AuditLogger(audit_db)
    audit.log_audit("req-z3", "default", "Z3_VIOLATION", success=False, details={"metric": "ARR"})
    audit.log_audit(
        "req-rh", "default", "INQUIRE_STREAM", success=True, details={"task_type": "redhat"}
    )

    from prompt_matrix.db.jdf_repository import save_jdf_revision

    save_jdf_revision(
        "default",
        {
            "document_id": "doc-default",
            "meta": {"source_files": [{"name": "cim.pdf", "sha256": "abc"}]},
            "truth_ledger": {"Revenue_ARR": 4200000},
            "body": [
                {
                    "id": "sec-1",
                    "title": "Summary",
                    "children": [
                        {
                            "id": "p-1",
                            "type": "paragraph",
                            "content": "Revenue $4.2M",
                            "provenance": [
                                {
                                    "source_type": "internal_doc",
                                    "source_name": "cim.pdf",
                                    "page_number": "12",
                                    "extracted_quote": "Revenue was $4.2M",
                                    "source_id": "prov-1",
                                }
                            ],
                        }
                    ],
                }
            ],
        },
        mutation_type="TEST",
    )

    res = client.get("/api/projects/default/export-audit")
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["ok"] is True
    manifest = payload["manifest"]
    assert manifest["project_id"] == "default"
    assert manifest["truth_ledger"]["Revenue_ARR"] == 4200000
    assert len(manifest["z3_logs"]) >= 1
    assert len(manifest["node_provenance"]) == 1
