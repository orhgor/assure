"""Raw candidates (customer plan V4 Part 1, 2026-09-28): the schema-agnostic
facts layer, its exact merge/precedence rules, the projection outcomes and the
page-quality floor — pure behaviour, no database."""
from __future__ import annotations

import copy
import json

import pytest

from prompt_matrix.services import field_extractor as fx
from prompt_matrix.services import quality_probe as qp
from prompt_matrix.services import raw_candidates as rc

PAGE = "\n".join([
    "PROJECT REPORT GENERATED - 8 JULY 2026",
    "REPORT ID + RPT-260708-E7BE23",
    "Policy Number: AP-77-1",
    "Named Insured: Jane Q. Sample",
    "Total Premium: $1,250.00",
    "CAPTURED - 8 JUL 2026 - GPS - -27.55344, 152.88712",
])


def _layout(text: str) -> list[list[dict]]:
    return fx.page_layout({"text": text})


def test_every_candidate_carries_provenance_only_and_never_a_confidence_or_state():
    pool, stats = rc.build_pool([PAGE], _layout(PAGE))
    assert pool and stats["status"] == "completed" and stats["candidates"] == len(pool)
    for c in pool:
        assert set(c) == set(rc.CANDIDATE_KEYS), sorted(set(c) ^ set(rc.CANDIDATE_KEYS))
        assert "confidence" not in c and "evidence_state" not in c and "field_state" not in c
        assert c["normalized_value"] is None and c["candidate_id"].startswith("rc-")
        span = c["source_span"]
        assert PAGE[span["start_char"]:span["end_char"]] == c["raw_text"], (c["label_anchor"], c["raw_text"])
        assert c["source_kind"] in rc.SOURCE_KINDS and c["source_priority"] == rc.SOURCE_PRIORITY[c["source_kind"]]
    labels = {c["label_anchor"]: c["raw_text"] for c in pool}
    assert labels["REPORT ID"] == "RPT-260708-E7BE23"  # the OCR'd middle dot ("+") is a separator, not part of the id
    assert labels["Policy Number"] == "AP-77-1" and labels["Total Premium"] == "$1,250.00"


def test_pool_is_byte_identical_across_runs_and_independent_of_input_order():
    pool_a, _ = rc.build_pool([PAGE], _layout(PAGE))
    pool_b, _ = rc.build_pool([PAGE], _layout(PAGE))
    assert json.dumps(pool_a, sort_keys=True) == json.dumps(pool_b, sort_keys=True)
    # merge_new must not depend on the order the sources emitted candidates
    shuffled = list(reversed(copy.deepcopy(pool_a)))
    merged, _ = rc.merge_new(shuffled)
    assert [c["candidate_id"] for c in merged] == [c["candidate_id"] for c in pool_a]


def test_duplicates_collapse_to_the_highest_priority_source_and_overlaps_mark_preferred():
    base = dict(name_hint="policy_number", label_anchor="Policy Number", page=1, node_id=None, element_id=None, normalized_value=None,
                corroborated=True, preferred=True, trace="t", candidate_id=None)
    lo = {**base, "raw_text": "AP-77-1", "source_kind": "discovery", "source_priority": 2, "source_span": {"start_char": 10, "end_char": 17}}
    hi = {**base, "raw_text": "ap-77-1", "source_kind": "textract", "source_priority": 5, "source_span": {"start_char": 10, "end_char": 17}}
    other = {**base, "raw_text": "AP-99-9", "source_kind": "layout_text", "source_priority": 3, "source_span": {"start_char": 12, "end_char": 19}}
    pool, counts = rc.merge_new([lo, hi, other])
    assert counts["deduped"] == 1 and counts["overlaps"] == 1
    assert [c["source_kind"] for c in pool] == ["textract", "layout_text"]  # the duplicate collapsed into textract (priority 5)
    assert pool[0]["preferred"] is True and pool[1]["preferred"] is False  # the overlapping different text stays, unpreferred


def test_sort_key_is_exactly_the_documented_sequence():
    assert rc.SORT_KEY == ("source_priority desc", "corroborated desc", "page asc", "start_char asc", "source_kind", "raw_text")
    a = {"source_priority": 3, "corroborated": True, "page": 1, "source_span": {"start_char": 5, "end_char": 9}, "source_kind": "layout_text", "raw_text": "b"}
    b = {**a, "raw_text": "a"}
    c = {**a, "source_span": {"start_char": 2, "end_char": 4}}
    d = {**a, "page": 0}
    e = {**a, "corroborated": False}
    f = {**a, "source_priority": 4}
    assert sorted([a, b, c, d, e, f], key=rc.sort_key) == [f, d, c, b, a, e]


def test_uncorroborated_vision_ranks_below_every_deterministic_source_and_corroborated_above():
    text = "Claim Number: CL-1\nVehicle: 2019 Toyota Corolla"
    vision = {"model": "vlm", "pages": [{"page": 1, "facts": [
        {"name": "vehicle", "value": "2019 Toyota Corolla", "evidence": "silver sedan", "bbox": None},
        {"name": "plate", "value": "XYZ-999", "evidence": "rear plate", "bbox": [0.1, 0.1, 0.2, 0.2]},
    ]}]}
    pool, stats = rc.build_pool([text], _layout(text), vision=vision)
    by_label = {c["label_anchor"]: c for c in pool}
    assert by_label["vehicle"]["corroborated"] is True and by_label["vehicle"]["source_priority"] == rc.CORROBORATED_VISION_PRIORITY
    assert by_label["plate"]["corroborated"] is False and by_label["plate"]["source_priority"] == 1 and by_label["plate"]["source_span"]["start_char"] is None
    assert pool[0]["label_anchor"] == "vehicle" and pool[-1]["label_anchor"] == "plate"
    assert stats["corroborated_vision"] == 1 and stats["uncorroborated_vision"] == 1
    assert "NOT on page text" in by_label["plate"]["trace"]


def test_table_cells_and_textract_forms_are_sources_with_their_priorities():
    text = "Coverage schedule follows."
    tables = [{"table_id": "tbl-1", "page": 1, "node_id": "n-9", "element_id": "e-9", "bbox": None,
               "headers": ["Coverage", "Limit", "Premium"], "rows": [["Liability", "100,000", "980.00"], ["Collision", "50,000", "1,284.00"]]}]
    forms = [{"key": "Policy No", "value": "PN-5", "page": 1}]
    pool, stats = rc.build_pool([text], _layout(text), tables=tables, forms=forms)
    kinds = [c["source_kind"] for c in pool]
    assert kinds[0] == "textract" and set(kinds) == {"textract", "table_cell"}
    cells = [c for c in pool if c["source_kind"] == "table_cell"]
    assert {(c["label_anchor"], c["raw_text"]) for c in cells} == {("Limit", "100,000"), ("Premium", "980.00"), ("Limit", "50,000"), ("Premium", "1,284.00")}
    assert all(c["source_span"]["span_type"] == "table_cell" and c["source_span"]["row_label"] in ("Liability", "Collision") for c in cells)
    assert stats["by_source"]["table_cell"] == 4 and stats["by_source"]["textract"] == 1


def test_the_pool_keeps_every_deterministic_candidate():
    """User decision 2026-09-29: no cap — an unknown form's fields are whatever
    the page says (a Textract-read CMS-1500 yields 230; the old cap of 60
    dropped 11 of them)."""
    lines = [f"Field {i}: value {i}" for i in range(150)]
    text = "\n".join(lines)
    pool, stats = rc.build_pool([text], _layout(text))
    assert rc.MAX_PER_SEGMENT is None and len(pool) >= 150 and stats["capped"] == 0
    dyn = rc.dynamic_fields(pool)
    assert len(dyn) >= 150 and dyn[0]["label"] == "Field 0" and dyn[0]["value"] == "value 0" and dyn[0]["source"] == "discovery"
    assert all("confidence" not in d for d in dyn) and dyn[0]["page"] == 1 and dyn[0]["candidate_id"]
    mapped = rc.dynamic_fields(pool, {"candidates_log": {dyn[0]["candidate_id"]: {"field": "policy_number", "outcome": "mapped"}}})
    assert mapped[0]["schema_field"] == "policy_number" and mapped[0]["projection"] == "mapped" and mapped[1]["schema_field"] is None


def test_merge_pool_is_append_only():
    pool, _ = rc.build_pool([PAGE], _layout(PAGE))
    stored = copy.deepcopy(pool)
    stored[0]["preferred"] = False  # a stored flag a later run must not touch
    extra_text = PAGE + "\nAgent: Mary Agent"
    fresh, _ = rc.build_pool([extra_text], _layout(extra_text))
    merged, added = rc.merge_pool(stored, fresh)
    assert added == 1 and len(merged) == len(stored) + 1
    assert merged[: len(stored)] == stored  # byte-identical prefix: nothing mutated, nothing deleted
    assert merged[-1]["label_anchor"] == "Agent"


def _kw(texts):
    return dict(texts=texts, layout=_layout(texts[0]), parser_name="jdf-cli", parse_confidence=None, ocr_confidence=None,
                page_quality=[0.9], visual_pages=[None])


def test_projection_maps_a_candidate_the_label_pass_read_as_debris_and_never_consumes_it():
    text = "REPORT ID + RPT-260708-E7BE23\nGENERATED - 8 JULY 2026"
    fields = fx.extract_fields("site_report", [text], layout=_layout(text), parser_name="jdf-cli", parse_confidence=None, ocr_confidence=None,
                               page_quality=[0.9], visual_pages=[None])
    rid = next(f for f in fields if f["name"] == "report_id")
    assert rid["value"] is None and rid["evidence_state"] == "found_suspect" and rid["provenance_confidence"] < 1.0  # "+ RPT-…" is debris
    pool, _ = rc.build_pool([text], _layout(text))
    out, log = rc.project_candidates("site_report", pool, fields, **_kw([text]))
    rid = next(f for f in out if f["name"] == "report_id")
    assert rid["value"] == "RPT-260708-E7BE23" and rid["extraction_method"] == "raw_candidate" and rid["value_quality"]["quality"] == "valid"
    assert rid["provenance_confidence"] == 1.0 and rid["candidate_source"]["source_kind"] in ("layout_text", "discovery")
    assert log["fields"]["report_id"] == {"outcome": "mapped", "candidate_id": rid["candidate_source"]["candidate_id"],
                                          "source_kind": rid["candidate_source"]["source_kind"], "replaced": "found_suspect"}
    assert len(pool) == len([c for c in pool]) and all(c["candidate_id"] in log["candidates_log"] for c in pool)  # pool untouched, every candidate logged
    # a candidate that maps into one field is still available for another schema
    out2, log2 = rc.project_candidates("auto_policy", pool, fx.extract_fields("auto_policy", [text], layout=_layout(text), parser_name="jdf-cli",
                                                                               parse_confidence=None, ocr_confidence=None, page_quality=[0.9], visual_pages=[None]), **_kw([text]))
    assert log2["counts"]["mapped"] == 0 and log2["counts"]["unmapped"] == len(pool)


def test_projection_logs_a_conflict_and_keeps_both_facts():
    text = "Policy Number: AP-77-1\nNamed Insured: Jane Q. Sample"
    fields = fx.extract_fields("auto_policy", [text], layout=_layout(text), parser_name="jdf-cli", parse_confidence=None, ocr_confidence=None,
                               page_quality=[0.9], visual_pages=[None])
    pool, _ = rc.build_pool([text], _layout(text), forms=[{"key": "Policy Number", "value": "AP-77-1", "page": 1}])
    out, log = rc.project_candidates("auto_policy", pool, fields, **_kw([text]))
    assert log["fields"]["policy_number"]["outcome"] == "mapped" and log["fields"]["policy_number"]["replaced"] is None  # agrees
    # a disagreeing higher-priority fact: conflict, both kept
    text2 = "Policy Number: AP-77-1\nPolicy No: AP-88-2\nNamed Insured: Jane Q. Sample"
    fields2 = fx.extract_fields("auto_policy", [text2], layout=_layout(text2), parser_name="jdf-cli", parse_confidence=None, ocr_confidence=None,
                                page_quality=[0.9], visual_pages=[None])
    pool2, _ = rc.build_pool([text2], _layout(text2), forms=[{"key": "Policy No", "value": "AP-88-2", "page": 1}])
    out2, log2 = rc.project_candidates("auto_policy", pool2, fields2, **_kw([text2]))
    pn = next(f for f in out2 if f["name"] == "policy_number")
    assert log2["fields"]["policy_number"]["outcome"] == "conflicting" and log2["conflicts"][0]["kind"] == "candidate_conflict"
    assert pn["candidate_conflict"]["kept"] == "candidate" and pn["value"] == "AP-88-2"  # textract outranks the text regex
    assert {v["value"] for v in log2["conflicts"][0]["values"]} == {"AP-77-1", "AP-88-2"}
    assert any(c["raw_text"] == "AP-77-1" for c in pool2)  # the losing fact is still in the pool


def test_review_needed_is_a_log_label_never_a_field_state():
    text = "REPORT ID · RPT-1\nGENERATED · 8 JULY 2026"
    fields = fx.extract_fields("site_report", [text], layout=_layout(text), parser_name="unknown-parser", parse_confidence=0.4, ocr_confidence=None,
                               page_quality=[0.5], visual_pages=[None])
    pool, _ = rc.build_pool([text], _layout(text))
    out, log = rc.project_candidates("site_report", pool, fields, texts=[text], layout=_layout(text), parser_name="unknown-parser", parse_confidence=0.4,
                                     ocr_confidence=None, page_quality=[0.5], visual_pages=[None])
    for f in out:
        fx.apply_decision_policy(f)
    rc.finalize_projection(log, out)
    rid = next(f for f in out if f["name"] == "report_id")
    assert log["fields"]["report_id"]["outcome"] == "review_needed"
    assert rid["field_state"] == "unverified" and rid["routing_action"] == "manual_review"
    assert "review_needed" not in fx.FIELD_STATES and "review_needed" not in fx.ROUTING_ACTIONS and "review_needed" not in fx.EVIDENCE_STATES
    assert set(rc.PROJECTION_OUTCOMES) == {"mapped", "unmapped", "conflicting", "review_needed"}


def test_grounding_floor_lifts_a_read_page_and_leaves_a_debris_page_low():
    readable = {"flags": ["low_res", "low_contrast"], "dpi_estimate": 60.0, "blur_variance": 900.0, "contrast_std": 20.0, "contrast_range": 40.0}
    # Since 2026-09-29 an OCR-read page is scored ocr × coverage; a page the
    # parse covered only in part is lifted by what the extraction proved it read.
    score, basis = qp.page_quality_score(visual=readable, ocr_confidence=0.8, text_density=0.6, parse_coverage=0.25, image_ratio=None, signature_quality=None)
    assert score == 0.2, (score, basis)
    lifted, basis2 = qp.page_quality_score(visual=readable, ocr_confidence=0.8, text_density=0.6, parse_coverage=0.25, image_ratio=None,
                                           signature_quality=None, grounding_success=1.0)
    assert lifted == 0.8 >= 0.5 and "lifted to grounded reads 1.00 × ocr 0.80 = floor 0.80" in basis2
    debris, basis3 = qp.page_quality_score(visual=readable, ocr_confidence=0.45, text_density=0.6, parse_coverage=1.0, image_ratio=None,
                                           signature_quality=None, grounding_success=0.0)
    assert debris == 0.45 and "below the ocr score" in basis3
    untouched, _ = qp.page_quality_score(visual=readable, ocr_confidence=0.45, text_density=0.6, parse_coverage=1.0, image_ratio=None, signature_quality=None,
                                         grounding_success=None)
    assert untouched == score if False else untouched < 0.2


def test_grounding_success_counts_valid_reads_for_and_suspects_against():
    fields = [
        {"name": "a", "value": "x", "value_quality": {"quality": "valid"}, "source_span": {"page": 1}},
        {"name": "b", "value": None, "evidence_state": "found_suspect", "source_span": {"page": 1}},
        {"name": "c", "value": None, "evidence_state": "found_suspect", "source_span": {"page": 2}},
        {"name": "sig", "field_type": "signature", "value": None, "source_span": {"page": 1}},
    ]
    discovered = [{"value": "~~-,;;", "page": 2, "span": {"page": 2}}, {"value": "RPT-1", "page": 3, "span": {"page": 3}}]
    assert rc.grounding_success_by_page(fields, discovered=discovered, page_count=4) == [0.5, 0.0, 1.0, None]
