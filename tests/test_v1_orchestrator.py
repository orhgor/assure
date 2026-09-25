"""run_after_parse builds and saves the V1 contract from a real parse bundle
shape (jdf-cli and PyMuPDF tree), logs the pipeline events, and never raises."""

from __future__ import annotations

import json

import pytest

from prompt_matrix.services import field_extractor as fx
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


# --------------------------------------------------------------------------
# Phase A: grounded LLM fill and mixed bundles (spec §6, §8 item 6)
# --------------------------------------------------------------------------

PROSE_LINES = [
    "PERSONAL AUTO POLICY DECLARATIONS",
    "Northstar Mutual Automobile Insurance Company",
    "Northstar Mutual issues this personal auto policy, numbered NAP-4471-2025, to Daniel R. Whitfield.",
    "The policy period runs from 03/01/2025 to 03/01/2026.",
    "The covered automobile is a 2003 Honda Accord EX Sedan, VIN 1HGCM82633A004352.",
    "Bodily injury liability is written at $250,000 each person; the collision deductible of $500 and the comprehensive deductible of $250 apply.",
    "The total annual premium for this policy is $1,486.00, countersigned by Marianne Costa.",
]

CLAIM_LINES = [
    "AUTOMOBILE LOSS NOTICE — FIRST NOTICE OF CLAIM",
    "Claim Number: CLM-2025-093311",
    "Claimant: Daniel R. Whitfield",
    "Date of Loss: 08/14/2025",
    "Location of Accident: Route 3A, Plymouth, MA",
    "Loss Description: Rear-ended at a red light; rear bumper damaged.",
    "Estimated Damage: $4,275.00",
    "Repair Shop: Cordage Park Collision",
    "Adjuster: Thomas Greeley",
]


def multi_page_bundle(*pages):
    jdf_pages, chunks = [], []
    for p, lines in enumerate(pages, start=1):
        elements = [{"id": f"p{p}-el-{i}", "type": "text", "content": line, "position": {"x": 25.4, "y": 20 + i * 6}, "width": 120} for i, line in enumerate(lines)]
        jdf_pages.append({"id": f"page-{p}", "pageSize": {"width": 209.9, "height": 297.04}, "elements": elements})
        chunks.append({"id": f"c{p}", "text": "\n".join(lines), "page": p, "types": ["text"]})
    return {"jdf": {"$jdf": "1.0", "meta": {}, "pages": jdf_pages}, "chunks": chunks, "text": "\f".join("\n".join(lines) for lines in pages),
            "page_count": len(pages), "parser_name": "jdf-cli", "source_kind": "pdf", "parse_confidence": None, "ocr_confidence": None,
            "tables": [], "images": [], "figures": [], "table_count": 0, "image_count": 0, "figure_count": 0,
            "asset_summary": {"tables": 0, "images": 0, "figures": 0}}


def test_llm_grounded_fill_merges_only_proven_values(db, monkeypatch):
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "1")
    prompts = []

    def fake(prompt):
        prompts.append(prompt)
        return json.dumps({
            "policy_number": {"quote": "numbered NAP-4471-2025, to", "value": "NAP-4471-2025"},
            "premium": {"quote": "total annual premium for this policy is $1,486.00", "value": "1486.00"},
            "liability_limit": {"quote": "Bodily injury liability is written at $250,000", "value": "$250,000"},
            "collision_deductible": {"quote": "collision deductible of $500", "value": "500"},
            "agent_name": {"quote": "countersigned by Marianne Costa.", "value": "Mary Agent"},  # value not in quote
            "comprehensive_deductible": {"quote": "the comprehensive deductible is $250", "value": "250"},  # quote not in text
        })

    out = _run(jdf_cli_bundle(PROSE_LINES), completion=fake)
    r = out["report"]
    assert len(prompts) == 1 and "=== PAGE 1 ===" in prompts[0]
    fields = {f["name"]: f for f in r["fields"]}
    assert fields["vin"]["extraction_method"] == "label_anchor"  # the label pass found it; not re-asked
    assert '"vin"' not in prompts[0]
    for name in ("policy_number", "premium", "liability_limit", "collision_deductible"):
        f = fields[name]
        assert f["extraction_method"] == "llm_grounded", name
        assert f["segment"] == 0 and f["source_span"]["span_type"] == "bbox_relative" and f["field_source_node_id"].startswith("el-")
        assert "× llm_grounded (0.85)" in f["confidence_basis"]
    assert fields["premium"]["value"] == 1486.0 and fields["liability_limit"]["value"] == 250000.0
    # Compliance-bound → still manual review by the existing policy; grounded confidence 0.72 < 0.75 keeps the rest in review too.
    assert (fields["premium"]["field_state"], fields["premium"]["routing_action"]) == ("unverified", "manual_review")
    assert fields["collision_deductible"]["extraction_confidence"] == pytest.approx(0.85 * 0.85, abs=0.02)
    for name in ("agent_name", "comprehensive_deductible"):
        assert fields[name]["value"] is None and fields[name]["reason"] == "field not found" and fields[name]["extraction_method"] is None
    assert sum("llm candidate rejected" in n for n in r["extraction_notes"]) == 2
    assert any("4 field(s) grounded, 2 candidate(s) rejected" in n for n in r["extraction_notes"])
    assert r["documents"] == [{"index": 0, "pages": [1], "document_type": "auto_policy", "confidence": r["classification"]["confidence"],
                               "basis": r["classification"]["basis"], "matched_keywords": r["classification"]["matched_keywords"],
                               "fields_total": len(r["fields"]), "fields_found": sum(1 for f in r["fields"] if f["value"] is not None)}]
    assert r["material_type"] == "pdf" and "mixed_bundle" not in r["quality_flags"]
    from prompt_matrix.db import parsure_repository as repo

    ev = next(e for e in repo.list_events("default", report_id=out["report_id"]) if e["event_type"] == "fields_extracted")
    assert ev["payload"]["llm_grounded"] == 4


def test_flag_off_means_no_model_call_from_the_orchestrator(db, monkeypatch):
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "0")
    calls = []
    r = _run(jdf_cli_bundle(PROSE_LINES), completion=lambda p: calls.append(p) or "{}")["report"]
    assert calls == []
    assert r["extraction_notes"] == ["llm extraction skipped: PARSURE_LLM_EXTRACTION is off"]
    assert all(f["extraction_method"] in (None, "label_anchor") for f in r["fields"])
    # Nothing missing → nothing asked, no note either way.
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "1")
    r = _run(jdf_cli_bundle(), completion=lambda p: calls.append(p) or "{}")["report"]
    assert calls == [] and r["extraction_notes"] == []


def test_classification_override_runs_label_pass_only(db, monkeypatch):
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "1")
    calls = []
    report = orch.build_report("default", bundle=jdf_cli_bundle(PROSE_LINES), verification=VERIFICATION, filename="p.pdf", result=RESULT,
                               job_id=None, intake=None, completion=lambda p: calls.append(p) or "{}")
    assert len(calls) == 1
    orch.reextract_for_type(report, "auto_claim", completion=lambda p: calls.append(p) or "{}")
    assert len(calls) == 1  # web-tier path: no model call
    assert report["extraction_notes"] == ["llm extraction skipped: not run on this path (label pass only)"]
    assert report["documents"][0]["document_type"] == "auto_claim" and all(f["segment"] == 0 for f in report["fields"])
    orch.reextract_for_type(report, "auto_claim", completion=lambda p: calls.append(p) or "{}", llm=True)
    assert len(calls) == 2


def test_unreachable_model_is_a_note_not_a_failure(db, monkeypatch):
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "1")

    def down(prompt):
        raise ConnectionError("connect: connection refused")

    r = _run(jdf_cli_bundle(PROSE_LINES), completion=down)["report"]
    assert r["extraction_notes"] == ["llm extraction skipped: ConnectionError: connect: connection refused"]
    assert all(f["extraction_method"] != "llm_grounded" for f in r["fields"])
    assert r["review_summary"]["fields_total"] == len(r["fields"])


def test_mixed_bundle_is_segmented_by_page_classification(db, monkeypatch):
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "0")
    coverages = ["COVERAGES AND LIMITS (continued)", "Bodily Injury Liability: $250,000 each person", "Collision Deductible: $500",
                 "Comprehensive Deductible: $250", "Total Premium: $1,486.00 annual", "Agent: Mary Agent", "Authorized Signature: /s/ Mary Agent"]
    page1 = [line for line in POLICY_LINES if not line.startswith(("Total Premium", "Liability", "Collision", "Comprehensive", "Agent", "Authorized"))]
    out = _run(multi_page_bundle(page1, coverages, CLAIM_LINES))
    r = out["report"]
    assert r["material_type"] == "mixed_bundle" and r["modality"] == "mixed" and "mixed_bundle" in r["quality_flags"]
    assert r["classification"]["document_type"] == "mixed_bundle" and "no visual boundary detection" in r["classification"]["basis"]
    assert [(d["index"], d["pages"], d["document_type"]) for d in r["documents"]] == [(0, [1, 2], "auto_policy"), (1, [3], "auto_claim")]
    assert all(0 < d["confidence"] <= 0.9 for d in r["documents"])
    fields = {(f["segment"], f["name"]): f for f in r["fields"]}
    assert fields[(0, "policy_number")]["value"] == "AP-2025-0001" and fields[(0, "policy_number")]["source_span"]["page"] == 1
    assert fields[(0, "premium")]["value"] == 1486.0 and fields[(0, "premium")]["source_span"]["page"] == 2
    assert fields[(0, "premium")]["field_source_node_id"] == "p2-el-4"
    assert fields[(1, "claim_number")]["value"] == "CLM-2025-093311" and fields[(1, "claim_number")]["source_span"]["page"] == 3
    assert fields[(1, "adjuster_name")]["value"] == "Thomas Greeley"
    assert fields[(1, "signature")]["signature_quality"]["page"] == 3  # segment's own last page, not the bundle's first
    assert r["documents"][0]["fields_total"] == len(fx.FIELD_TAXONOMY["auto_policy"]) and r["documents"][1]["fields_total"] == len(fx.FIELD_TAXONOMY["auto_claim"])
    assert r["review_summary"]["fields_total"] == len(r["fields"]) == r["documents"][0]["fields_total"] + r["documents"][1]["fields_total"]
    assert {rule["segment"] for rule in r["plausibility"]} == {0, 1}
    assert "2 documents detected in one upload (auto_policy p.1–2, auto_claim p.3)" in r["quality_report"]["summary"]
    # Single-type multi-page uploads stay one segment.
    single = _run(multi_page_bundle(page1, coverages), result={"document_id": "doc-2", "revision_id": "rev-2", "version": 2})["report"]
    assert len(single["documents"]) == 1 and single["documents"][0]["pages"] == [1, 2] and single["material_type"] == "pdf"
    assert single["classification"]["document_type"] == "auto_policy" and "mixed_bundle" not in single["quality_flags"]


def test_segment_pages_rules():
    policy = "\n".join(POLICY_LINES)
    claim = "\n".join(CLAIM_LINES)
    blank = "continued on next page"
    # uncertain pages join the running segment; a leading uncertain page joins the first confident one
    segs = orch.segment_pages([blank, policy, blank, claim])
    assert [(s["pages"], s["document_type"]) for s in segs] == [([1, 2, 3], "auto_policy"), ([4], "auto_claim")]
    # same type on every confident page → one segment, whole-document classification
    segs = orch.segment_pages([policy, blank, policy])
    assert len(segs) == 1 and segs[0]["pages"] == [1, 2, 3] and segs[0]["document_type"] == "auto_policy"
    # all uncertain → one segment
    assert orch.segment_pages([blank, blank])[0]["document_type"] == "uncertain"
    assert orch.segment_pages([])[0]["pages"] == []
    assert orch.segment_texts(["a", "b", "c", "d"], [2, 3]) == ["", "b", "c"]
