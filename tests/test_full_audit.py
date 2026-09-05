"""Macro Red-Hat appendix / Full Audit unified compiler."""

from __future__ import annotations

from pathlib import Path

from prompt_matrix.i18n import CATALOGS, LOCALES
from prompt_matrix.services.audit_summary import build_audit_summary
from prompt_matrix.services.confidence_spans import build_macro_appendix

ROOT = Path(__file__).resolve().parents[1]

KEYS = (
    "generate.full_audit",
    "generate.audit_manifest",
    "generate.audit_manifest.claim",
    "generate.audit_manifest.z3",
    "generate.audit_manifest.redhat",
    "generate.audit_manifest.empty",
    "generate.audit_manifest.pending",
    "generate.audit_manifest.pending_z3",
    "generate.audit_manifest.pending_redhat",
    "tooltip.full_audit",
)


def test_full_audit_i18n_keys() -> None:
    for locale in LOCALES:
        cat = CATALOGS[locale]
        for key in KEYS:
            assert key in cat, f"missing {locale} {key}"
            assert str(cat[key]).strip()
    assert CATALOGS["en"]["generate.full_audit"] == "Full Audit"
    assert CATALOGS["tr"]["generate.full_audit"] == "Tam denetim"


def test_full_audit_markup() -> None:
    html = (ROOT / "prompt_matrix" / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="generate-full-audit-btn"' in html
    assert 'data-i18n="generate.full_audit"' in html
    assert 'id="jdf-audit-appendix"' in html
    assert 'data-i18n="generate.audit_manifest"' in html
    assert "gettext('Full Audit')" in html
    assert "gettext('Audit Manifest')" in html


def test_full_audit_js_wires_parallel_redhat() -> None:
    js = (ROOT / "prompt_matrix" / "static" / "generate.js").read_text(encoding="utf-8")
    assert "fullAudit" in js
    assert "generate-full-audit-btn" in js
    assert "runRedhatStress({ parallel: true })" in js
    assert "parallel: !!opts.parallel" in js
    nav = (ROOT / "prompt_matrix" / "static" / "app_nav.js").read_text(encoding="utf-8")
    assert "opts.parallel" in nav
    canvas = (ROOT / "prompt_matrix" / "static" / "jdf_canvas.js").read_text(encoding="utf-8")
    assert "renderAuditAppendix" in canvas


def test_build_macro_appendix_pairs_score_and_critique() -> None:
    document = {
        "document_id": "doc-1",
        "meta": {},
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
                        "content": "Revenue reached 12 million.",
                        "annotations": {
                            "redhat": [
                                {
                                    "id": "c1",
                                    "text": "Source the 12 million figure.",
                                    "status": "open",
                                }
                            ]
                        },
                    }
                ],
            }
        ],
    }
    z3 = {
        "status": "PASS",
        "violations": [],
        "lock_results": [{"key": "Revenue", "ok": True, "value": 12_000_000}],
    }
    rows = build_macro_appendix(document, z3_results=z3, redhat_critiques=[])
    assert rows
    claim = next(r for r in rows if "Revenue" in r["claim"])
    assert claim["z3Score"] > 0.8
    assert "12 million" in claim["redhatCritique"]


def test_audit_summary_includes_manifest() -> None:
    document = {
        "document_id": "doc-1",
        "meta": {},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "s1",
                "title": "A",
                "children": [{"type": "paragraph", "id": "p1", "content": "Hello world."}],
            }
        ],
    }
    summary = build_audit_summary(
        z3_results={"status": "PASS", "violations": [], "lock_results": []},
        redhat_critiques=[{"title": "Red-hat", "content": "Push on Hello."}],
        document=document,
    )
    assert summary["claims"]
    assert summary["audit_manifest"] == summary["claims"]
    assert any("Hello" in row["claim"] for row in summary["claims"])
    assert any("Push on Hello" in (row["redhatCritique"] or "") for row in summary["claims"])
