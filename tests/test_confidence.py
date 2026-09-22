"""Confidence scoring: layer separation, Red-Hat, structured assets, honesty."""

from __future__ import annotations

from prompt_matrix.services.confidence import (
    add_confidence_to_jdf,
    compute_confidence,
)


def _doc(nodes):
    return {
        "meta": {},
        "body": [
            {
                "type": "section",
                "id": "s1",
                "title": "A",
                "children": nodes,
            }
        ],
    }


def _para(node_id="p1", content="Revenue is up.", **extra):
    node = {"type": "paragraph", "id": node_id, "content": content}
    node.update(extra)
    return node


def test_parse_and_verification_quality_stay_separate():
    """Parse quality tracks reported parse confidence; verification quality
    tracks the audit stack. One moving does not move the other's meaning."""
    meta = {
        "parser_name": "jdf-cli",
        "parse_confidence": 0.95,
        "ocr_confidence": None,
        "table_count": 1,
        "image_count": 1,
        "figure_count": 1,
        "asset_summary": {"tables": 1, "images": 1, "figures": 1},
    }
    nodes_with_provenance = [
        _para(
            provenance=[{"source_id": "s", "entailment": {"verdict": "supported"}}],
            annotations={"z3": [{"status": "pass"}]},
        )
    ]
    strong_verification = compute_confidence(
        _doc(nodes_with_provenance), substrate_meta=meta
    )
    weak_verification = compute_confidence(_doc([_para()]), substrate_meta=meta)

    # Same parse input, different verification: verification_quality differs
    # while parse_quality stays at the parser-reported level.
    assert (
        strong_verification["verification_quality"]
        > weak_verification["verification_quality"]
    )
    assert strong_verification["parse_quality"] == weak_verification["parse_quality"]
    # They are distinct fields, never merged
    assert (
        strong_verification["parse_quality"]
        != strong_verification["verification_quality"]
    )
    assert strong_verification["parse_quality"] == 95.0
    # document_quality is the only combination, present at the end
    assert (
        strong_verification["document_quality"]
        != strong_verification["parse_quality"]
    )


def test_redhat_open_findings_reduce_score_and_appear_in_reasons():
    clean = compute_confidence(
        _doc([_para(annotations={"redhat": [{"status": "resolved", "text": "x"}]})])
    )
    flagged = compute_confidence(
        _doc(
            [
                _para(
                    annotations={
                        "redhat": [
                            {
                                "status": "open",
                                "severity": "high",
                                "text": "unsupported claim",
                            }
                        ]
                    }
                )
            ]
        )
    )
    assert flagged["document_quality"] < clean["document_quality"]
    assert flagged["verification_quality"] < clean["verification_quality"]
    assert flagged["breakdown"]["redhat"] < clean["breakdown"]["redhat"]
    assert any("Red-Hat" in r for r in flagged["reasons"])
    # The flagged node is a named low-confidence node with a Red-Hat reason
    assert flagged["low_confidence_nodes"]
    assert any("Red-Hat" in n["reason"] for n in flagged["low_confidence_nodes"])


def test_missing_tables_images_figures_lower_structured_quality():
    with_assets = compute_confidence(
        _doc(
            [
                _para(),
                {"type": "table", "id": "t1", "headers": [], "rows": []},
                {"type": "image", "id": "i1", "src": ""},
                {
                    "type": "image",
                    "id": "f1",
                    "src": "",
                    "meta": {"asset_kind": "figure"},
                },
            ]
        ),
        substrate_meta={
            "parser_name": "jdf-cli",
            "table_count": 1,
            "image_count": 1,
            "figure_count": 1,
        },
    )
    without_assets = compute_confidence(
        _doc([_para()]),
        substrate_meta={
            "parser_name": "jdf-cli",
            "table_count": 0,
            "image_count": 0,
            "figure_count": 0,
        },
    )
    assert without_assets["structured_quality"] < with_assets["structured_quality"]
    kinds = {m["kind"] for m in without_assets["missing_assets"]}
    assert kinds == {"table", "image", "figure"}
    assert with_assets["missing_assets"] == []
    assert any("no table" in r for r in without_assets["reasons"])


def test_unknown_confidence_stays_none_not_zero():
    """No parse metadata: the parse dimension says unknown (neutral baseline,
    named in reasons), never an optimistic fabricated 85 or a dropped 0."""
    report = compute_confidence(_doc([_para()]))
    assert report["parse_quality"] == 60.0
    assert any("unknown" in r for r in report["reasons"])


def test_zero_parse_confidence_is_scored_as_zero():
    """A parser-reported 0.0 is data, not unknown: it scores as 0, and the
    reason names it — truthiness guards must not turn it into 'unknown'."""
    report = compute_confidence(
        _doc([_para()]),
        substrate_meta={"parse_confidence": 0.0, "parser_name": "jdf-cli"},
    )
    assert report["breakdown"]["parse"] == 0.0
    assert "parse confidence 0" in " ".join(report["reasons"])


def test_confidence_report_shape():
    report = compute_confidence(_doc([_para()]))
    assert set(report) >= {
        "document_quality",
        "parse_quality",
        "structured_quality",
        "verification_quality",
        "breakdown",
        "low_confidence_nodes",
        "low_confidence_pages",
        "missing_assets",
        "reasons",
    }
    assert set(report["breakdown"]) == {
        "parse",
        "ocr",
        "tables",
        "figures",
        "images",
        "layout",
        "provenance",
        "entailment",
        "z3",
        "redhat",
    }
    assert isinstance(report["document_quality"], float)


def test_add_confidence_to_jdf_writes_all_layers():
    jdf = _doc([_para()])
    report = compute_confidence(jdf)
    add_confidence_to_jdf(jdf, report)
    meta = jdf["meta"]
    assert meta["confidence"] == report["document_quality"]
    assert meta["confidenceBreakdown"] == report["breakdown"]
    assert meta["lowConfidenceNodes"] == report["low_confidence_nodes"]
    assert meta["lowConfidencePages"] == report["low_confidence_pages"]
    assert meta["missingAssets"] == report["missing_assets"]
    assert meta["parseQuality"] == report["parse_quality"]
    assert meta["structuredQuality"] == report["structured_quality"]
    assert meta["verificationQuality"] == report["verification_quality"]