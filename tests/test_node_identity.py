"""Stable element identity (``eid-v1``, 2026-09-26).

jdf-cli 0.2.3 elements carry no id and the tree paragraph id is
``new_node_id`` (random per run), so a replay could never name the node a
value came from twice — the customer benchmark's P0 "missing stable element
IDs". ``field_extractor.derive_element_id`` is derived from the chunk id, the
bbox (3 dp) and the collapsed text only: same bytes, same id, in any process.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys

import pytest

from prompt_matrix.services import field_extractor as fx
from prompt_matrix.services import jdf_converter as jc
from prompt_matrix.services import v1_orchestrator as orch

LINES = [
    "AUTO INSURANCE POLICY DECLARATIONS\nPolicy Number: PA-4471-9902\nNamed Insured: Jordan Avery",
    "Effective Date: 03/15/2026 Expiration Date: 09/15/2026\nVehicle: 2003 Honda Accord EX",
    "VIN: 1HGCM82633A004352\nTotal Premium: $1,284.00",
    "Bodily Injury Liability Limit: $100,000 per person / $300,000 per accident\nCollision Deductible: $500",
    "Comprehensive Deductible: $250\nAgent: Riverside Insurance Agency",
    "Authorized Signature: ______________________",
]


def jdf_and_chunks(lines=LINES):
    """The shape ``jdf convert`` + ``jdf chunk --strategy section`` produce for
    a one-page declarations PDF (measured on /tmp/auto_policy.pdf, 2026-09-26:
    6 elements without ids, one chunk ``p1e0`` joining them with blank lines)."""
    elements = [
        {"type": "text", "content": line, "position": {"x": 21.17, "y": 25.2 + i * 10.66}, "width": 99.1 + i, "style": {"fontSize": 11}}
        for i, line in enumerate(lines)
    ]
    jdf = {"$jdf": "1.0", "meta": {}, "pages": [{"id": "page-1", "pageSize": {"width": 215.9, "height": 279.4}, "elements": elements}]}
    chunks = [{"id": "p1e0", "text": "\n\n".join(lines), "page": 1, "types": ["text"], "tokens": 100, "hash": "abc"}]
    return jdf, chunks


def bundle_for(jdf, chunks):
    return {"jdf": jdf, "chunks": chunks, "text": jc.chunks_to_text(chunks), "page_count": 1, "parser_name": "jdf-cli",
            "source_kind": "pdf", "parse_confidence": None, "ocr_confidence": None}


def found(fields):
    return {f["name"]: f for f in fields if f.get("value") is not None}


def _extract(bundle):
    layout = fx.page_layout(bundle)
    texts = fx.page_texts(bundle)
    return fx.extract_fields("auto_policy", texts, layout=layout, parser_name="jdf-cli", parse_confidence=None, ocr_confidence=None, page_quality=[1.0])


# --------------------------------------------------------------------------
# The derivation rule
# --------------------------------------------------------------------------

def test_derivation_is_chunk_id_bbox_3dp_and_collapsed_text_sha1_12():
    eid = fx.derive_element_id("p1e0", 1, [0.09812, 0.09019, 0.55711, 0.10688], "VIN:  1HGCM82633A004352\nTotal   Premium")
    digest = hashlib.sha1("0.098,0.090,0.557,0.107|VIN: 1HGCM82633A004352 Total Premium".encode()).hexdigest()[:12]
    assert eid == f"p1e0:{digest}"
    assert fx.NODE_ID_POLICY == "eid-v1"
    assert fx.NODE_ID_DERIVATION == "chunk_id + bbox(3dp) + text sha1[:12]"
    # no chunk → the page is the prefix; no bbox → an empty bbox part, still deterministic
    assert fx.derive_element_id(None, 3, None, "x y").startswith("p3:")
    assert fx.derive_element_id(None, 3, None, "x y") == fx.derive_element_id(None, 3, None, "x   y")


def test_changed_text_or_bbox_is_a_different_element_and_noise_is_not():
    base = fx.derive_element_id("p1e0", 1, [0.1, 0.2, 0.3, 0.4], "Policy Number: PA-1")
    assert fx.derive_element_id("p1e0", 1, [0.1, 0.2, 0.3, 0.4], "Policy Number: PA-2") != base
    assert fx.derive_element_id("p1e0", 1, [0.1, 0.25, 0.3, 0.4], "Policy Number: PA-1") != base
    assert fx.derive_element_id("p1e1", 1, [0.1, 0.2, 0.3, 0.4], "Policy Number: PA-1") != base
    # below 3 dp and whitespace shape: the same element
    assert fx.derive_element_id("p1e0", 1, [0.1002, 0.2004, 0.2996, 0.4001], "Policy  Number:\nPA-1") == base


def test_no_uuid_no_clock_two_processes_agree():
    """The same inputs in a fresh interpreter give the same id — the property
    a replay across workers needs, which ``new_node_id`` never had."""
    args = ("p2e3", 2, [0.5, 0.5, 0.75, 0.52], "Claim Number: CLM-2025-093311")
    here = fx.derive_element_id(*args)
    code = ("import json,sys; from prompt_matrix.services.field_extractor import derive_element_id as d; "
            "a=json.loads(sys.argv[1]); print(d(*a))")
    out = subprocess.run([sys.executable, "-c", code, json.dumps(args)], capture_output=True, text=True, check=True,
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert out.stdout.strip() == here
    # and the paragraph id it is *not* derived from is random per run
    from prompt_matrix.models.jdf import new_node_id
    assert new_node_id("p") != new_node_id("p")


# --------------------------------------------------------------------------
# Replay: same bundle twice, same text with a regenerated tree
# --------------------------------------------------------------------------

def test_parsing_the_same_bundle_twice_yields_identical_element_ids():
    jdf, chunks = jdf_and_chunks()
    a = found(_extract(bundle_for(copy.deepcopy(jdf), copy.deepcopy(chunks))))
    b = found(_extract(bundle_for(copy.deepcopy(jdf), copy.deepcopy(chunks))))
    assert a and set(a) == set(b)
    for name in a:
        assert a[name]["element_id"] == b[name]["element_id"] == a[name]["source_span"]["element_id"]
        assert a[name]["element_id"].startswith("p1e0:")
    # the elements are told apart: policy number and VIN sit on different lines
    assert a["policy_number"]["element_id"] != a["vin"]["element_id"]
    assert len({f["element_id"] for f in a.values()}) >= 5


def test_regenerated_tree_names_the_same_elements_as_the_raw_parse():
    """Import path: the saved tree replaces ``bundle["jdf"]`` and its paragraph
    ids are new every time; the element ids inside ``meta.elements`` are not."""
    jdf, chunks = jdf_and_chunks()
    raw = found(_extract(bundle_for(jdf, chunks)))
    tree1 = jc.jdf_to_document_tree(jdf, chunks, document_id="d", title="t")
    tree2 = jc.jdf_to_document_tree(jdf, chunks, document_id="d", title="t")
    p1 = tree1["body"][0]["children"][0]
    p2 = tree2["body"][0]["children"][0]
    assert p1["id"] != p2["id"]  # random paragraph ids, as before
    assert [e["element_id"] for e in p1["meta"]["elements"]] == [e["element_id"] for e in p2["meta"]["elements"]]
    via_tree1 = found(_extract({**bundle_for(jdf, chunks), "jdf": tree1}))
    via_tree2 = found(_extract({**bundle_for(jdf, chunks), "jdf": tree2}))
    assert set(raw) == set(via_tree1) == set(via_tree2)
    for name in raw:
        assert raw[name]["element_id"] == via_tree1[name]["element_id"] == via_tree2[name]["element_id"]
        assert via_tree1[name]["source_span"]["element_id"] == raw[name]["element_id"]


def test_changed_text_changes_only_the_touched_element():
    jdf, chunks = jdf_and_chunks()
    lines = list(LINES)
    lines[2] = "VIN: 1HGCM82633A004352\nTotal Premium: $1,999.00"
    jdf2, chunks2 = jdf_and_chunks(lines)
    a = found(_extract(bundle_for(jdf, chunks)))
    b = found(_extract(bundle_for(jdf2, chunks2)))
    assert a["premium"]["element_id"] != b["premium"]["element_id"]
    assert a["vin"]["element_id"] != b["vin"]["element_id"]  # same line as the premium
    assert a["policy_number"]["element_id"] == b["policy_number"]["element_id"]
    assert a["agent_name"]["element_id"] == b["agent_name"]["element_id"]


# --------------------------------------------------------------------------
# Report surface
# --------------------------------------------------------------------------

def test_report_carries_policy_identity_element_counts_and_offsets(monkeypatch):
    monkeypatch.setenv("PARSURE_LLM_EXTRACTION", "0")
    jdf, chunks = jdf_and_chunks()
    tree = jc.jdf_to_document_tree(jdf, chunks, document_id="d", title="t")
    r = orch.build_report("p", bundle={**bundle_for(jdf, chunks), "jdf": tree}, verification=None, filename="ap.pdf", result={},
                          job_id=None, intake=None, tree=tree)
    assert r["node_id_policy"] == "eid-v1"
    assert r["identity"] == {"policy": "eid-v1", "derivation": "chunk_id + bbox(3dp) + text sha1[:12]"}
    gi = r["graph_integrity"]
    assert gi["policy"] == "eid-v1" and gi["orphans"] == 0
    f = found(r["fields"])
    assert gi["element_ids"] == len(f) >= 5
    content = tree["body"][0]["children"][0]["content"]
    for name, field in f.items():
        span = field["source_span"]
        assert span["element_id"] == field["element_id"] and span["node_id"] == field["tree_node_id"]
        off = span["node_offsets"]
        assert content[off["start_char"]:off["end_char"]] == field["raw"], name
        assert span["span_type"] == "bbox_relative" and len(span["bbox"]) == 4
    assert all(f2["element_id"] is None for f2 in r["fields"] if f2.get("value") is None)  # nothing invented for absent fields
