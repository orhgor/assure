"""run_after_parse builds and saves the V1 contract from a real parse bundle
shape (jdf-cli and PyMuPDF tree), logs the pipeline events, and never raises."""

from __future__ import annotations

import pytest

from prompt_matrix.services import v1_orchestrator as orch

POLICY_LINES = [
    "AUTO POLICY DECLARATIONS",
    "Policy Number: AP-2025-0001",
    "Named Insured: John Q. Sample",
    "Policy Period: 01/15/2025 to 01/15/2026",
    "Vehicle: 2003 Honda Accord",
    "VIN: 1HGCM82633A004352",
    "Total Premium: $1,250.00",
    "Liability Limit: $100,000",
    "Collision Deductible: $500",
    "Comprehensive Deductible: $250",
    "Agent: Mary Agent",
    "Authorized Signature: /s/ Mary Agent",
]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "orch.sqlite"))
    monkeypatch.setenv("ASSURE_DATA_DIR", str(tmp_path))
    import prompt_matrix.history as history_mod

    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.db.jdf_repository import ensure_project

    init_db()
    ensure_project("default")
    yield


def jdf_cli_bundle(lines=POLICY_LINES, ocr_conf=None):
    elements = []
    for i, line in enumerate(lines):
        el = {"id": f"el-{i}", "type": "text", "content": line, "position": {"x": 25.4, "y": 20 + i * 6}, "width": 120, "style": {"fontSize": 11}}
        elements.append(el)
    return {
        "jdf": {"$jdf": "1.0", "meta": {"title": "policy"}, "pages": [{"id": "page-1", "pageSize": {"width": 209.9, "height": 297.04}, "elements": elements}]},
        "chunks": [{"id": "c1", "text": "\n".join(lines), "page": 1, "types": ["text"]}],
        "text": "\n".join(lines), "page_count": 1, "parser_name": "jdf-cli", "source_kind": "pdf",
        "parse_confidence": None, "ocr_confidence": ocr_conf, "tables": [], "images": [], "figures": [],
        "table_count": 0, "image_count": 0, "figure_count": 0, "asset_summary": {"tables": 0, "images": 0, "figures": 0},
    }


def pymupdf_bundle(lines=POLICY_LINES):
    tree = {"document_id": "doc-default", "meta": {}, "truth_ledger": {}, "body": [
        {"type": "section", "id": "sec-1", "title": "Page 1", "meta": {"source_page": 1},
         "children": [{"type": "paragraph", "id": f"p-{i}", "content": line} for i, line in enumerate(lines)]},
    ]}
    return {"jdf": tree, "chunks": [], "text": "", "page_count": 1, "parser_name": "pymupdf", "source_kind": "pdf",
            "parse_confidence": None, "ocr_confidence": None, "tables": [], "images": [], "figures": [],
            "table_count": 0, "image_count": 0, "figure_count": 0, "asset_summary": {"tables": 0, "images": 0, "figures": 0}}


VERIFICATION = {"z3": {"z3_status": "PASS", "violations": [], "checked_at": "2026-09-25T00:00:00+00:00"}, "z3_status": "PASS", "redhat_status": "complete"}
RESULT = {"document_id": "doc-default", "revision_id": "rev-1", "version": 1}


def _run(bundle, **kw):
    params = dict(bundle=bundle, verification=VERIFICATION, filename="policy.pdf", file_bytes=None, result=RESULT, job_id="job-1", intake=None)
    params.update(kw)
    return orch.run_after_parse("default", **params)


def test_jdf_cli_bundle_becomes_a_saved_contract(db):
    out = _run(jdf_cli_bundle())
    assert out and out["report_id"].startswith("pr-")
    r = out["report"]
    assert r["schema_version"] == "1.0" and r["policy_version"] == "v1"
    assert r["parser_name"] == "jdf-cli" and r["parser_version"] == orch.current_jdf_cli_version()
    assert r["verification_version"] and r["material_type"] == "pdf" and r["modality"] == "digital_pdf"
    assert r["document_id"] == "doc-default" and r["revision_id"] == "rev-1" and r["job_id"] == "job-1"
    assert r["page_count"] == 1 and r["pages"][0]["page"] == 1
    assert "basis" in r["pages"][0]
    assert r["classification"]["document_type"] == "auto_policy" and r["classification"]["override"] is None
    assert "_page_texts" not in r  # private key stripped from the public shape
    fields = {f["name"]: f for f in r["fields"]}
    assert fields["policy_number"]["value"] == "AP-2025-0001"
    assert fields["vin"]["source_span"]["span_type"] == "bbox_relative" and fields["vin"]["field_source_node_id"] == "el-5"
    for f in r["fields"]:
        assert set(f) >= {"name", "label", "field_type", "value", "raw", "extraction_confidence", "confidence_basis", "verification_confidence",
                          "provenance_confidence", "signature_quality", "number_quality", "source_span", "field_source_node_id",
                          "field_state", "routing_action", "review_required", "reason", "z3_violation", "compliance_bound", "corrected"}
    assert r["review_summary"]["fields_total"] == len(r["fields"])
    assert r["review_summary"]["fields_accepted"] + r["review_summary"]["fields_review"] >= r["review_summary"]["fields_total"] - r["review_summary"]["fields_rejected"]
    assert isinstance(r["quality_report"]["summary"], str) and r["quality_report"]["summary"].endswith(".")
    assert r["replay"]["replayed"] is False and r["replay"]["history"] == []
    assert isinstance(r["plausibility"], list) and len(r["plausibility"]) == 6
    assert r["conflicts"] == []
    assert r["laya"] is None

    from prompt_matrix.db import parsure_repository as repo

    stored = repo.get_report("default", out["report_id"])
    assert stored["_page_texts"] == ["\n".join(POLICY_LINES)]
    events = [e["event_type"] for e in repo.list_events("default", report_id=out["report_id"])]
    assert set(events) == {"intake_received", "quality_assessed", "classified", "fields_extracted", "decision_applied"}


def test_pymupdf_tree_bundle_uses_text_range_spans_and_node_ids(db):
    out = _run(pymupdf_bundle())
    r = out["report"]
    assert r["parser_name"] == "pymupdf"
    fields = {f["name"]: f for f in r["fields"]}
    assert fields["premium"]["value"] == 1250.0
    assert fields["premium"]["source_span"]["span_type"] == "text_range"
    assert fields["premium"]["field_source_node_id"] == "p-6"


def test_intake_router_output_flows_into_pages_and_laya(db):
    intake = {
        "parser": "jdf", "material_type": "pdf", "modality": "scanned_pdf", "source_kind": "scanned",
        "visual_pages": [{"page": 1, "dpi_estimate": 120.0, "blur_variance": 900.0, "contrast_std": 60.0, "flags": ["low_res"], "basis": "raster 120 dpi"}],
        "laya": {"model": "rules-v1", "policy_version": "v1", "suggested_route": "textract", "escalate": False, "human_review": True, "reasons": ["low_res"]},
    }
    out = _run(jdf_cli_bundle(ocr_conf=0.7), intake=intake)
    r = out["report"]
    assert r["modality"] == "scanned_pdf" and r["laya"]["model"] == "rules-v1"
    assert r["pages"][0]["flags"] == ["low_res"] and r["pages"][0]["visual"]["dpi_estimate"] == 120.0
    assert r["pages"][0]["ocr_confidence"] == 0.7
    assert "low_res" in r["quality_flags"]
    assert "Low resolution on 1 of 1 page" in r["quality_report"]["summary"]
    if r["document_quality_score"] is not None:
        assert 0 <= r["document_quality_score"] <= 1


def test_replay_eligibility_reasons_are_real(db):
    out = _run(jdf_cli_bundle(), intake={"visual_pages": [{"page": 1, "dpi_estimate": 60.0, "blur_variance": 10.0, "contrast_std": 5.0, "flags": ["low_res", "blurry", "low_contrast"], "basis": "bad"}]})
    r = out["report"]
    low = [f for f in r["fields"] if f["value"] is not None and f["extraction_confidence"] < orch.REPLAY_LOW_CONFIDENCE]
    assert r["replay"]["eligible"] is bool(low or any(f["corrected"] for f in r["fields"]))
    if low:
        assert any("low extraction confidence" in reason for reason in r["replay"]["reasons"])


def test_conflicts_are_recomputed_across_project_reports(db):
    _run(jdf_cli_bundle())
    other = [line.replace("AP-2025-0001", "AP-9999-0002") for line in POLICY_LINES]
    out = _run(jdf_cli_bundle(other), result={"document_id": "doc-2", "revision_id": "rev-2", "version": 2})
    conflicts = out["report"]["conflicts"]
    assert [c["field"] for c in conflicts] == ["policy_number"]
    assert {v["value"] for v in conflicts[0]["values"]} == {"AP-2025-0001", "AP-9999-0002"}


def test_z3_violation_in_verification_rejects_the_field(db):
    verification = {"z3": {"z3_status": "VIOLATION", "violations": [{"severity": "high", "category": "Z3 Contradiction", "description": "premium ledger mismatch", "node_id": "el-6"}]},
                    "z3_status": "VIOLATION", "redhat_status": "complete"}
    r = _run(jdf_cli_bundle(), verification=verification)["report"]
    premium = next(f for f in r["fields"] if f["name"] == "premium")
    assert premium["z3_violation"] is True
    assert (premium["field_state"], premium["routing_action"]) == ("rejected", "compliance_review")
    assert r["verification"]["z3_status"] == "VIOLATION" and r["verification"]["z3_violation_count"] == 1
    assert r["review_summary"]["fields_rejected"] >= 1


def test_run_after_parse_never_raises(db, monkeypatch):
    monkeypatch.setattr(orch, "build_report", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _run(jdf_cli_bundle()) is None


def test_empty_bundle_yields_uncertain_report_without_values(db):
    out = _run({"jdf": None, "chunks": [], "text": "", "page_count": 1, "parser_name": "textract", "source_kind": "scanned", "parse_confidence": None, "ocr_confidence": None, "images": []})
    r = out["report"]
    assert r["classification"]["document_type"] == "uncertain"
    assert r["fields"] == [] and r["parser_name"] == "textract" and r["parser_version"] == orch.TEXTRACT_VERSION
    assert "no_text" in r["quality_flags"]
