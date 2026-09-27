"""Table-aware extraction (plan Part 3 / 7.5): jdf-cli table elements become
``report["tables"]``, their cells fill the money/number/date fields the label
pass left empty, and the field record is the same contract shape."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from prompt_matrix.services import field_extractor as fx
from prompt_matrix.services import table_extraction as te
from prompt_matrix.services import v1_orchestrator as orch
from tests.test_v1_orchestrator import RESULT, VERIFICATION, db  # noqa: F401  (fixture)

SCHEDULE_LINES = [
    "SCHEDULE OF COVERAGES AND FORMS — HOMEOWNERS HO-3",
    "Bay Colony Property & Casualty Insurance Company",
    "Policy Number: HO3-55120-PL",
    "Named Insured: Rosa and Miguel Alvarez",
    "Insured Location: 42 Sandwich Road, Plymouth, MA 02360",
    "Policy Period: 05/01/2025 to 05/01/2026",
]
SCHEDULE_FOOTER = [
    "Total Annual Premium: $2,140.00",
    "Forms and endorsements made part of this policy: HO 00 03 05 11, HO 04 61 05 11.",
    "Agent: Harbor Insurance Associates",
]
#: The grid exactly as jdf-cli 0.2.3 emitted it for bench/cases coverage_schedule.pdf
#: (2026-09-27): ruled column lines produce blank-header phantom columns that hold
#: the value of the header to their left.
RAW_HEADERS = ["Coverage", "Form", "Limit", "", "Deductible", "", "Premium"]
RAW_ROWS = [
    ["Coverage A · Dwelling", "HO 00 03", "", "$425,000", "", "$2,500", "$1,412.00"],
    ["Coverage B · Other Structures", "HO 00 03", "", "$42,500", "", "$2,500", "$96.00"],
    ["Coverage C · Personal Property", "HO 00 03", "", "$212,500", "", "$2,500", "$318.00"],
    ["Coverage D · Loss of Use", "HO 00 03", "", "$85,000", "", "·", "$44.00"],
    ["Water Backup", "HO 04 95", "", "$10,000", "", "$1,000", "$48.00"],
]


def table_element(headers, rows, *, y=56.66):
    return {"type": "table", "position": {"x": 17.61, "y": y}, "width": 180.68, "headers": headers, "rows": rows, "style": {"fontSize": 8.5}}


def table_bundle(lines=SCHEDULE_LINES, footer=SCHEDULE_FOOTER, headers=RAW_HEADERS, rows=RAW_ROWS):
    elements = []
    for i, line in enumerate(lines):
        elements.append({"type": "text", "content": line, "position": {"x": 17.6, "y": 20 + i * 6}, "width": 120, "style": {"fontSize": 10}})
    elements.append(table_element(headers, rows))
    for i, line in enumerate(footer):
        elements.append({"type": "text", "content": line, "position": {"x": 17.6, "y": 120 + i * 6}, "width": 120, "style": {"fontSize": 10}})
    text = "\n".join(lines + footer)
    chunks = [
        {"id": "p1e0", "text": "\n".join(lines), "page": 1, "types": ["text"]},
        {"id": "p1e1", "text": "Coverage: … | Premium: …", "page": 1, "types": ["table"]},
        {"id": "p1e2", "text": "\n".join(footer), "page": 1, "types": ["text"]},
    ]
    return {
        "jdf": {"$jdf": "1.0", "meta": {}, "pages": [{"id": "page-1", "pageSize": {"width": 215.9, "height": 279.4}, "elements": elements}]},
        "chunks": chunks, "text": text, "page_count": 1, "parser_name": "jdf-cli", "source_kind": "pdf",
        "parse_confidence": None, "ocr_confidence": None, "tables": [{"id": "p1e1", "text": chunks[1]["text"], "page": 1}],
        "images": [], "figures": [], "table_count": 1, "image_count": 0, "figure_count": 0,
        "asset_summary": {"tables": 1, "images": 0, "figures": 0},
    }


# --------------------------------------------------------------------------- #
# collect_tables
# --------------------------------------------------------------------------- #

def test_collect_tables_repairs_phantom_columns_and_names_the_table():
    tables = te.collect_tables(table_bundle())
    assert len(tables) == 1
    t = tables[0]
    assert t["headers"] == ["Coverage", "Form", "Limit", "Deductible", "Premium"]
    assert t["rows"][0] == ["Coverage A · Dwelling", "HO 00 03", "$425,000", "$2,500", "$1,412.00"]
    assert t["layout_repair"]["columns_merged"] == 2 and t["layout_repair"]["columns_dropped"] == 2
    assert t["page"] == 1 and t["node_id"] == "p1e1" and t["chunk_id"] == "p1e1" and t["source"] == "jdf_element"
    assert t["element_id"].startswith("p1e1:") and t["table_id"] == "tbl-" + t["element_id"]
    assert t["id_policy"] == fx.NODE_ID_POLICY
    assert t["bbox"] and len(t["bbox"]) == 4 and t["bbox"][3] > t["bbox"][1] and "estimated" in t["bbox_basis"]
    assert t["quality"]["status"] == "ok" and t["quality"]["row_count"] == 5 and t["quality"]["header_count"] == 5
    assert set(t) >= {"table_id", "node_id", "element_id", "page", "bbox", "headers", "rows", "caption", "quality"}


def test_table_ids_are_deterministic_across_runs():
    a = te.collect_tables(table_bundle())[0]
    b = te.collect_tables(json.loads(json.dumps(table_bundle())))[0]
    assert a["table_id"] == b["table_id"] and a["element_id"] == b["element_id"]
    moved = te.collect_tables(table_bundle(rows=RAW_ROWS[:-1]))[0]
    assert moved["table_id"] != a["table_id"]  # different content is a different table


def test_collect_tables_reads_tree_table_nodes_and_ignores_bundles_without_tables():
    tree = {"jdf": {"document_id": "d", "meta": {}, "truth_ledger": {}, "body": [
        {"type": "section", "id": "sec-1", "title": "Page 1", "meta": {"source_page": 1}, "children": [
            {"type": "paragraph", "id": "p-1", "content": "Policy Number: X"},
            {"type": "table", "id": "tbl-1", "caption": "Coverages", "headers": ["Coverage", "Limit"], "rows": [["Dwelling", "$100"]]},
        ]},
    ]}}
    tables = te.collect_tables(tree)
    assert len(tables) == 1 and tables[0]["node_id"] == "tbl-1" and tables[0]["source"] == "tree_node" and tables[0]["caption"] == "Coverages"
    assert tables[0]["bbox"] is None
    assert te.collect_tables({"jdf": {"pages": [{"elements": [{"type": "text", "content": "x"}]}]}}) == []
    assert te.collect_tables({"text": "flat"}) == []


def test_assess_table_quality_statuses():
    ok = te.assess_table_quality({"headers": ["A", "B"], "rows": [["1", "2"], ["3", "4"]]})
    assert ok["status"] == "ok" and ok["score"] == 1.0 and ok["issues"] == []
    irregular = te.assess_table_quality({"headers": ["A", "B"], "rows": [["1", "2"], ["3"]]})
    assert irregular["status"] == "irregular_rows" and "irregular_rows" in irregular["issues"] and irregular["score"] == 0.8
    no_headers = te.assess_table_quality({"headers": [], "rows": [["1", "2"]]})
    assert no_headers["status"] == "no_headers" and no_headers["score"] == 0.7
    empty = te.assess_table_quality({"headers": ["A"], "rows": []})
    assert empty["status"] == "unreadable" and empty["score"] == 0.0 and "no rows" in empty["basis"]
    blank = te.assess_table_quality({"headers": ["A", "B"], "rows": [["", ""], ["", ""]]})
    assert blank["status"] == "unreadable"
    sparse = te.assess_table_quality({"headers": ["A", "B", "C"], "rows": [["1", "", ""], ["2", "", ""]]})
    assert sparse["status"] == "ok" and "many_empty_cells" in sparse["issues"] and sparse["score"] == 0.8
    assert all(s in te.TABLE_QUALITY_STATUSES for s in ("ok", "irregular_rows", "no_headers", "unreadable"))


# --------------------------------------------------------------------------- #
# extract_table_fields / fill_from_tables
# --------------------------------------------------------------------------- #

def _specs(*names, doc_type="schedule_of_forms"):
    by = {s.name: s for s in fx.FIELD_TAXONOMY[doc_type]}
    return [by[n] for n in names]


def test_row_label_column_and_ambiguous_picks():
    tables = te.collect_tables(table_bundle())
    fields = te.extract_table_fields("schedule_of_forms", tables, _specs("dwelling_coverage", "personal_property_coverage", "deductible", "premium"),
                                     parser_name="jdf-cli", page_quality=[None])
    by = {f["name"]: f for f in fields}
    assert by["dwelling_coverage"]["value"] == 425000.0 and by["dwelling_coverage"]["evidence"]["pick"] == "row_label"
    assert by["dwelling_coverage"]["grounding_quote"] == "Coverage A · Dwelling — Limit: $425,000"
    assert by["personal_property_coverage"]["value"] == 212500.0 and by["personal_property_coverage"]["source_span"]["row"] == 2
    # Deductible column: rows disagree, no total row → first row, confidence factor applied and stated.
    d = by["deductible"]
    assert d["value"] == 2500.0 and d["evidence"]["pick"] == "first_row" and "table_ambiguous_row (0.85)" in d["confidence_basis"]
    assert d["extraction_confidence"] < fx.EXTRACTION_THRESHOLD
    # Premium column: rows disagree → first row too (the label pass finds the total on the page and the pass is never asked in practice).
    assert by["premium"]["evidence"]["pick"] == "first_row"


def test_total_row_single_row_and_unanimous_picks():
    spec = _specs("premium")[0]
    total = te.collect_tables(table_bundle(headers=["Coverage", "Premium"], rows=[["A", "$10"], ["B", "$20"], ["Total", "$30"]]))
    got = te.extract_table_fields("schedule_of_forms", total, [spec])[0]
    assert got["value"] == 30.0 and got["evidence"]["pick"] == "total_row" and "table_ambiguous_row" not in got["confidence_basis"]
    single = te.collect_tables(table_bundle(headers=["Coverage", "Premium"], rows=[["A", "$10"]]))
    assert te.extract_table_fields("schedule_of_forms", single, [spec])[0]["evidence"]["pick"] == "single_row"
    same = te.collect_tables(table_bundle(headers=["Coverage", "Premium"], rows=[["A", "$10"], ["B", "$10.00"]]))
    assert te.extract_table_fields("schedule_of_forms", same, [spec])[0]["evidence"]["pick"] == "unanimous"
    none = te.collect_tables(table_bundle(headers=["Coverage", "Form"], rows=[["A", "HO 00 03"]]))
    assert te.extract_table_fields("schedule_of_forms", none, [spec]) == []


def test_table_field_is_the_contract_shape_with_table_provenance():
    tables = te.collect_tables(table_bundle())
    f = te.extract_table_fields("schedule_of_forms", tables, _specs("dwelling_coverage"), parser_name="jdf-cli", page_quality=[0.9])[0]
    empty = fx._empty_field(_specs("dwelling_coverage")[0])
    assert set(f) >= set(empty)  # never a second shape
    assert f["extraction_method"] == "table" and "table" in fx.EXTRACTION_METHODS
    assert f["raw"] == "$425,000" and f["value"] == 425000.0 and f["provenance_confidence"] == 1.0
    span = f["source_span"]
    assert span["span_type"] == "table_cell" and span["page"] == 1 and span["table_id"] == tables[0]["table_id"]
    assert span["row"] == 0 and span["col"] == 2 and span["element_id"] == tables[0]["element_id"] and span["bbox"] == tables[0]["bbox"]
    assert f["field_source_node_id"] == "p1e1" and f["element_id"] == tables[0]["element_id"]
    assert f["grounding_span"] == {"page": 1, "table_id": tables[0]["table_id"], "row": 0, "col": 2, "element_id": tables[0]["element_id"], "node_id": "p1e1"}
    assert f["grounding_model"] == "table" and f["grounding_source"] == "table"
    assert f["evidence"]["kind"] == "found" and f["evidence"]["method"] == "table" and f["evidence"]["table_quality"] == "ok"
    assert f["number_quality"]["quality"] == "printed_good"
    assert f["field_state"] == "unverified" and f["routing_action"] == "manual_review"
    assert "parser_default[jdf-cli] (0.85) × page_quality (0.90)" in f["confidence_basis"]
    assert f["value_quality"]["quality"] == "valid" and f["quality_source"] == "page_quality"


def test_fill_from_tables_only_fills_fields_the_label_pass_left_empty():
    bundle = table_bundle()
    tables = te.collect_tables(bundle)
    texts = fx.page_texts(bundle)
    fields = fx.extract_fields("schedule_of_forms", texts, parser_name="jdf-cli", parse_confidence=None, ocr_confidence=None,
                               page_quality=[None], layout=fx.page_layout(bundle))
    before = {f["name"]: f["value"] for f in fields}
    assert before["premium"] == 2140.0 and before["dwelling_coverage"] is None and before["deductible"] is None
    filled, stats = te.fill_from_tables("schedule_of_forms", fields, tables, parser_name="jdf-cli", page_quality=[None])
    after = {f["name"]: f for f in filled}
    assert after["premium"]["value"] == 2140.0 and after["premium"]["extraction_method"] == "label_anchor"  # untouched
    assert after["dwelling_coverage"]["value"] == 425000.0 and after["dwelling_coverage"]["extraction_method"] == "table"
    assert after["deductible"]["value"] == 2500.0
    assert stats == {"status": "completed", "tables": 1, "fields_offered": 3, "fields_from_tables": 3,
                     "fields_filled": ["dwelling_coverage", "personal_property_coverage", "deductible"], "reason": None}
    assert [f["name"] for f in filled] == [f["name"] for f in fields]  # order kept
    # No tables: nothing changes, and the block says why.
    same, none = te.fill_from_tables("schedule_of_forms", [dict(f) for f in fields], [])
    assert none["status"] == "not_run" and none["fields_from_tables"] == 0 and "no table" in none["reason"]
    # Text fields are never offered to a table.
    assert all(fx.FIELD_TAXONOMY["schedule_of_forms"][i].field_type in te.TABLE_FIELD_TYPES for i, f in enumerate(fields) if f["name"] in stats["fields_filled"])


def test_run_table_pass_outside_a_scope_is_not_run_and_never_raises():
    notes: list[str] = []
    execution: dict = {}
    fields = [fx._empty_field(_specs("deductible")[0])]
    out = te.run_table_pass("schedule_of_forms", fields, texts=["x"], execution=execution, notes=notes, parser_name=None,
                            parse_confidence=None, ocr_confidence=None, page_quality=[None], visual_pages=[None])
    assert out is fields and execution["tables"]["status"] == "not_run" and "no extraction scope" in execution["tables"]["reason"]
    with te.extraction_scope(te.collect_tables(table_bundle())):
        out = te.run_table_pass("schedule_of_forms", [fx._empty_field(_specs("deductible")[0])], texts=["x"], execution=execution, notes=notes,
                                parser_name="jdf-cli", parse_confidence=None, ocr_confidence=None, page_quality=[None], visual_pages=[None])
    assert out[0]["value"] == 2500.0 and execution["tables"]["fields_from_tables"] == 1 and notes and notes[0].startswith("table pass: 1 field")
    # a table on a page this segment does not cover is not used
    with te.extraction_scope(te.collect_tables(table_bundle())):
        out = te.run_table_pass("schedule_of_forms", [fx._empty_field(_specs("deductible")[0])], texts=["", "page two"], execution={}, notes=[],
                                parser_name="jdf-cli", parse_confidence=None, ocr_confidence=None, page_quality=[None, None], visual_pages=[None, None])
    assert out[0]["value"] is None


# --------------------------------------------------------------------------- #
# End to end: the report carries tables[] and execution.tables
# --------------------------------------------------------------------------- #

def test_report_carries_tables_execution_and_table_fields(db):  # noqa: F811
    out = orch.run_after_parse("default", bundle=table_bundle(), verification=VERIFICATION, filename="coverage_schedule.pdf", file_bytes=None,
                               result=RESULT, job_id="job-t", intake={"parser": "jdf", "material_type": "pdf", "modality": "digital_pdf", "claim_context": "renewal review"})
    r = out["report"]
    assert r["classification"]["document_type"] == "schedule_of_forms"
    assert len(r["tables"]) == 1 and r["tables"][0]["headers"] == ["Coverage", "Form", "Limit", "Deductible", "Premium"]
    ex = r["execution"]["tables"]
    assert ex["status"] == "completed" and ex["tables"] == 1 and ex["fields_from_tables"] == 3 and ex["reason"] is None
    assert set(ex) >= {"status", "tables", "fields_from_tables", "reason"}
    fields = {f["name"]: f for f in r["fields"]}
    assert fields["dwelling_coverage"]["value"] == 425000.0 and fields["dwelling_coverage"]["extraction_method"] == "table"
    assert fields["dwelling_coverage"]["field_source_node_id"] == "p1e1" and fields["dwelling_coverage"]["source_span"]["span_type"] == "table_cell"
    assert fields["premium"]["value"] == 2140.0 and fields["premium"]["extraction_method"] == "label_anchor"
    assert fields["deductible"]["review_required"] is True  # ambiguous column pick never auto-accepts
    assert r["graph_integrity"]["orphans"] == 0
    assert r["review_summary"]["fields_found"] >= 9
    assert any(n.startswith("table pass: 3 field") for n in r["extraction_notes"])
    # Part 4.3: the intake key the report did not consume is on the audit trail.
    assert r["intake_extra"] == {"claim_context": "renewal review"}
    assert r["discovered_fields"] == [] and r["execution"]["discovery"]["status"] == "not_run"
    # Re-extraction for another type keeps the tables and re-runs the pass.
    from prompt_matrix.db import parsure_repository as repo

    stored = repo.get_report("default", out["report_id"])
    again = orch.reextract_for_type(stored, "property_policy")
    got = {f["name"]: f for f in again["fields"]}
    assert got["dwelling_coverage"]["value"] == 425000.0 and got["dwelling_coverage"]["extraction_method"] == "table"
    assert again["execution"]["tables"]["fields_from_tables"] == 3 and len(again["tables"]) == 1


def test_report_without_tables_says_not_run(db):  # noqa: F811
    from tests.test_v1_orchestrator import jdf_cli_bundle

    out = orch.run_after_parse("default", bundle=jdf_cli_bundle(), verification=VERIFICATION, filename="policy.pdf", file_bytes=None, result=RESULT, job_id="j", intake=None)
    r = out["report"]
    assert r["tables"] == [] and r["execution"]["tables"]["status"] == "not_run" and r["execution"]["tables"]["tables"] == 0
    assert r["intake_extra"] is None


@pytest.mark.skipif(shutil.which("jdf") is None, reason="jdf-cli not installed locally")
def test_real_jdf_cli_bundle_of_the_bench_coverage_schedule():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
    try:
        from cases import generators as G
    finally:
        sys.path.pop(0)
    from prompt_matrix.services import jdf_converter as jc

    _, pdf, _ = G.GENERATORS["coverage_schedule_table_typed"]()
    bundle = jc.pdf_to_parse_bundle(pdf, filename="coverage_schedule.pdf", ocr="none")
    tables = te.collect_tables(bundle)
    assert len(tables) == 1 and tables[0]["headers"] == ["Coverage", "Form", "Limit", "Deductible", "Premium"] and tables[0]["node_id"] == "p1e2"
    fields = fx.extract_fields("schedule_of_forms", fx.page_texts(bundle), parser_name="jdf-cli", parse_confidence=None, ocr_confidence=None,
                               page_quality=[None], layout=fx.page_layout(bundle))
    filled, stats = te.fill_from_tables("schedule_of_forms", fields, tables, parser_name="jdf-cli", page_quality=[None])
    by = {f["name"]: f["value"] for f in filled}
    assert by["dwelling_coverage"] == 425000.0 and by["personal_property_coverage"] == 212500.0 and by["deductible"] == 2500.0 and by["premium"] == 2140.0
    assert stats["fields_from_tables"] == 3
