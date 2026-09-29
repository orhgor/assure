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


# --------------------------------------------------------------------------
# Grids reconstructed from shape rules (jdf-cli 0.2.5 emits no table element)
# --------------------------------------------------------------------------

def _rect(x, y, w, h):
    return {"type": "shape", "shape": "rect", "position": {"x": x, "y": y}, "width": w, "height": h, "stroke": {"color": "#000", "width": 0.2}}


def _line(x, y, w, h):
    return {"type": "shape", "shape": "line", "position": {"x": x, "y": y}, "width": w, "height": h, "stroke": {"color": "#000", "width": 0.2}}


def _text(x, y, content, size=8):
    return {"type": "text", "content": content, "position": {"x": x, "y": y}, "width": 30, "style": {"fontSize": size}}


GRID_CELLS = [["Coverage", "Limit", "Premium"], ["Dwelling", "$425,000", "$1,412.00"], ["Other Structures", "$42,500", "$96.00"]]


def _rect_grid_page(*, x0=20.0, y0=50.0, cw=45.0, rh=8.0):
    elements = []
    for r, row in enumerate(GRID_CELLS):
        for c, cell in enumerate(row):
            elements.append(_rect(x0 + c * cw, y0 + r * rh, cw, rh))
            elements.append(_text(x0 + c * cw + 1.5, y0 + r * rh + 1.0, cell))
    elements.append(_text(x0, y0 - 6, "SCHEDULE OF COVERAGES"))  # a caption above the grid, outside it
    return {"pageSize": {"width": 210, "height": 297}, "elements": elements}


def test_grid_from_rect_shapes_is_a_table_with_the_first_row_as_header():
    page = _rect_grid_page()
    bundle = {"jdf": {"pages": [page]}, "chunks": [{"id": "p1e0", "page": 1, "text": "SCHEDULE OF COVERAGES\nCoverage Limit Premium\nDwelling $425,000 $1,412.00", "types": ["text"]}]}
    tables = te.collect_tables(bundle)
    assert len(tables) == 1
    t = tables[0]
    assert t["source"] == "grid_from_shapes" and t["headers"] == GRID_CELLS[0] and t["rows"] == GRID_CELLS[1:]
    assert t["page"] == 1 and t["chunk_id"] == "p1e0" and t["table_id"].startswith("tbl-p1e0:")
    assert t["bbox"] == [round(20 / 210, 4), round(50 / 297, 4), round(155 / 210, 4), round(74 / 297, 4)]
    assert "reconstructed grid" in t["bbox_basis"]
    assert t["quality"]["status"] == "ok" and "grid reconstructed from 4 horizontal and 4 vertical shape rules" in t["quality"]["basis"]
    assert "cell text by text-element position" in t["quality"]["basis"]
    assert t["grid"] == {"horizontal_rules": 4, "vertical_rules": 4, "tolerance_mm": te.GRID_EDGE_TOL_MM, "row_bands": 3, "columns": 3}
    # the same grid twice names the same table
    assert te.collect_tables(bundle)[0]["table_id"] == t["table_id"]


def test_grid_from_line_shapes_like_jdf_cli_0_2_5():
    # rules drawn as hairline ``line`` shapes: 3 horizontal (y 50/58/66) × 3 vertical (x 20/65/110), cells hold text
    elements = [_line(20, y, 90, 0.1) for y in (50, 58, 66)] + [_line(x, 50, 0.1, 16) for x in (20, 65, 110)]
    for r, row in enumerate([["Item", "Amount"], ["Deductible", "$500"]]):
        for c, cell in enumerate(row):
            elements.append(_text(20 + c * 45 + 1.5, 50 + r * 8 + 1.0, cell))
    page = {"pageSize": {"width": 210, "height": 297}, "elements": elements}
    tables = te.collect_tables({"jdf": {"pages": [page]}, "chunks": []})
    assert len(tables) == 1
    assert tables[0]["headers"] == ["Item", "Amount"] and tables[0]["rows"] == [["Deductible", "$500"]]
    assert tables[0]["source"] == "grid_from_shapes" and tables[0]["chunk_id"] is None and tables[0]["table_id"].startswith("tbl-p1:")


def test_grid_on_top_of_a_table_element_is_not_reported_twice():
    page = _rect_grid_page()
    page["elements"].append(table_element(GRID_CELLS[0], GRID_CELLS[1:], y=50.0) if "y" in table_element.__code__.co_varnames else table_element(GRID_CELLS[0], GRID_CELLS[1:]))
    page["elements"][-1]["position"] = {"x": 20.0, "y": 50.0}
    page["elements"][-1]["width"] = 135.0
    tables = te.collect_tables({"jdf": {"pages": [page]}, "chunks": []})
    assert [t["source"] for t in tables] == ["jdf_element"]


def test_checkboxes_ruled_blank_boxes_and_pages_without_rules_make_no_table():
    boxes = {"pageSize": {"width": 210, "height": 297}, "elements": [_rect(10 + i * 4, 30, 2.97, 3.88) for i in range(6)] + [_text(10, 40, "MEDICARE MEDICAID")]}
    assert te.collect_tables({"jdf": {"pages": [boxes]}, "chunks": []}) == []
    blank = _rect_grid_page()
    blank["elements"] = [e for e in blank["elements"] if e["type"] != "text"]
    assert te.collect_tables({"jdf": {"pages": [blank]}, "chunks": []}) == []
    assert te.grid_tables_from_shapes({"pageSize": {"width": 210, "height": 297}, "elements": [_text(10, 10, "no rules here")]}, 1) == []


HCFA_0_2_5 = Path("/tmp/jdfcli/432938035-HCFA1500-10-Arial-Blue-1155-1-0.2.5.jdf")
HCFA_0_2_3 = Path("/tmp/jdfcli/432938035-HCFA1500-10-Arial-Blue-1155-1-0.2.3.jdf")


@pytest.mark.skipif(not (HCFA_0_2_5.exists() and HCFA_0_2_3.exists()), reason="converted HCFA-1500 demo form not present under /tmp/jdfcli")
def test_hcfa_form_cells_are_read_from_0_2_5_shapes_as_from_0_2_3_table_elements():
    """The demo HCFA-1500 converted by both jdf-cli versions (2026-09-28): 0.2.3
    emits 16 table elements, 0.2.5 none — the patient box must still come out
    as a grid cell holding the patient's name and address."""
    new = te.collect_tables({"jdf": json.loads(HCFA_0_2_5.read_text()), "chunks": []})
    old = te.collect_tables({"jdf": json.loads(HCFA_0_2_3.read_text()), "chunks": []})
    assert new and all(t["source"] == "grid_from_shapes" for t in new)
    assert sum(1 for t in old if t["source"] == "jdf_element") == 16
    cells_new = [c for t in new for row in [t["headers"]] + t["rows"] for c in row]
    cells_old = [c for t in old if t["source"] == "jdf_element" for row in t["rows"] for c in row]
    assert any("Martinez Gail D." in c for c in cells_new) and any("Martinez Gail D." in c for c in cells_old)
    assert any("8794 Main Street" in c for c in cells_new)


def test_a_garbage_table_cell_keeps_its_span_but_not_its_value_or_full_provenance():
    """Plan V5 R1 (2026-09-29): the table builder set ``provenance_confidence``
    to a constant 1.0 after judging the value's shape, so a header or debris
    read out of a table carried full provenance — the V1 defect surviving in
    the one builder ``PROVENANCE_BY_SHAPE`` had not reached."""
    rows = [
        ["Coverage A · Dwelling", "HO 00 03", "", "DWELLING LIMIT", "", "$2,500", "$1,412.00"],
        ["Coverage B · Other Structures", "HO 00 03", "", "$42,500", "", "$2,500", "$96.00"],
    ]
    tables = te.collect_tables(table_bundle(rows=rows))
    spec = _specs("dwelling_coverage")[0]
    table = tables[0]
    header_cell = {"row": 0, "col": 3, "raw": "DWELLING LIMIT", "value": None, "pick": "row_label", "basis": "row label matched"}
    f = te.build_table_field(spec, table, header_cell, parser_name="jdf-cli", parse_confidence=None, ocr_confidence=None, page_quality=[0.9], visual_pages=[None])
    assert f["value_quality"]["quality"] != "valid"
    assert f["value"] is None and f["evidence_state"] == "found_suspect"
    assert f["provenance_confidence"] == fx.PROVENANCE_BY_SHAPE[f["value_quality"]["quality"]] and f["provenance_confidence"] <= 0.7
    assert f["source_span"]["span_type"] == "table_cell" and f["raw"] == "DWELLING LIMIT"  # the span stays: the reviewer is taken to the debris
    assert f["confidence_basis"].startswith("not computed:")
    good = te.build_table_field(spec, table, {"row": 1, "col": 3, "raw": "$42,500", "value": 42500.0, "pick": "row_label", "basis": "row label matched"},
                                parser_name="jdf-cli", parse_confidence=None, ocr_confidence=None, page_quality=[0.9], visual_pages=[None])
    assert good["value"] == 42500.0 and good["value_quality"]["quality"] == "valid" and good["provenance_confidence"] == 1.0
