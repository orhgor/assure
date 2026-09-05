"""Z3 confidence spans + overlay wiring."""

from __future__ import annotations

from pathlib import Path

from prompt_matrix.i18n import CATALOGS, LOCALES
from prompt_matrix.services.audit_summary import build_audit_summary
from prompt_matrix.services.confidence_spans import build_confidence_spans

ROOT = Path(__file__).resolve().parents[1]

KEYS = (
    "jdf.confidence.toggle",
    "jdf.confidence.verified",
    "jdf.confidence.uncertain",
    "jdf.confidence.hallucination",
)


def test_confidence_i18n_keys() -> None:
    for locale in LOCALES:
        cat = CATALOGS[locale]
        for key in KEYS:
            assert key in cat, f"missing {locale} {key}"
            assert str(cat[key]).strip()


def test_build_confidence_spans_scores_from_z3() -> None:
    document = {
        "document_id": "doc-1",
        "meta": {"title": "Draft"},
        "truth_ledger": {"Revenue": 12_000_000},
        "body": [
            {
                "type": "section",
                "id": "s1",
                "title": "Results",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "p1",
                        "content": "Revenue reached 12 million. The moon is cheese.",
                    }
                ],
            }
        ],
    }
    z3 = {
        "status": "VIOLATION",
        "violations": ["Metric 'moon' = 1 contradicts locked == 0"],
        "lock_results": [{"key": "Revenue", "ok": True, "value": 12_000_000}],
    }
    spans = build_confidence_spans(document, z3_results=z3)
    by_text = {
        document["body"][0]["children"][0]["content"][s["startChar"] : s["endChar"]]: s
        for s in spans
        if s.get("nodeId") == "p1"
    }
    rev = next(s for t, s in by_text.items() if "Revenue" in t)
    moon = next(s for t, s in by_text.items() if "moon" in t)
    assert rev["score"] > 0.8
    assert rev["source"] == "z3"
    assert moon["score"] < 0.4
    assert moon["source"] == "z3"


def test_audit_summary_includes_confidence_spans() -> None:
    document = {
        "document_id": "doc-1",
        "meta": {},
        "truth_ledger": {"Revenue": 1},
        "body": [
            {
                "type": "section",
                "id": "s1",
                "title": "A",
                "children": [{"type": "paragraph", "id": "p1", "content": "Revenue is up."}],
            }
        ],
    }
    summary = build_audit_summary(
        z3_results={
            "status": "PASS",
            "violations": [],
            "lock_results": [{"key": "Revenue", "ok": True}],
        },
        redhat_critiques=[],
        document=document,
    )
    assert summary["confidenceSpans"]
    assert summary["confidence_spans"] == summary["confidenceSpans"]
    assert summary["document"]["meta"]["confidenceSpans"] == summary["confidenceSpans"]
    assert summary["confidenceSpans"][0]["startChar"] >= 0
    assert "score" in summary["confidenceSpans"][0]
    assert "source" in summary["confidenceSpans"][0]


def test_overlay_markup_and_scripts() -> None:
    html = (ROOT / "prompt_matrix" / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="confidence-overlay-toggle"' in html
    assert 'data-i18n="jdf.confidence.toggle"' in html
    assert "confidence_highlighter.js" in html
    js = (ROOT / "prompt_matrix" / "static" / "confidence_highlighter.js").read_text(
        encoding="utf-8"
    )
    assert "bg-green-200" in js
    assert "bg-yellow-200" in js
    assert "bg-red-200" in js
    assert "AssureConfidenceHighlighter" in js
    canvas = (ROOT / "prompt_matrix" / "static" / "jdf_canvas.js").read_text(encoding="utf-8")
    assert "setShowConfidenceOverlay" in canvas
    assert "_paintConfidence" in canvas
    gen = (ROOT / "prompt_matrix" / "static" / "generate.js").read_text(encoding="utf-8")
    assert "setConfidenceSpans" in gen
    css = (ROOT / "prompt_matrix" / "static" / "style.css").read_text(encoding="utf-8")
    assert ".bg-green-200" in css
    assert ".bg-yellow-200" in css
    assert ".bg-red-200" in css
