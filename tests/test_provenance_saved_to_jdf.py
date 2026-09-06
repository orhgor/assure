"""Provenance meta persisted on JDF nodes after audit summary."""

from __future__ import annotations

from prompt_matrix.models.jdf import empty_annotations
from prompt_matrix.services.audit_summary import build_audit_summary


def _sample_tree() -> dict:
    return {
        "document_id": "doc-prov",
        "meta": {"title": "Provenance test"},
        "truth_ledger": {"revenue": 100},
        "body": [
            {
                "type": "section",
                "id": "sec-1",
                "title": "Summary",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "para-1",
                        "content": "Revenue is 100 per NAIC policy.",
                        "provenance": [
                            {
                                "source_type": "internal_doc",
                                "source_name": "NAIC_Underwriting_Policy_2025.pdf",
                                "source_id": "sub-1",
                                "page_number": "4",
                                "extracted_quote": "revenue is 100",
                                "url_or_doi": "",
                                "accessed_date": "",
                            }
                        ],
                        "meta": {},
                        "annotations": empty_annotations(),
                    }
                ],
                "meta": {},
                "annotations": empty_annotations(),
            }
        ],
    }


def test_provenance_saved_to_jdf_meta() -> None:
    tree = _sample_tree()
    z3 = {
        "status": "PASS",
        "violations": [],
        "lock_results": [{"key": "revenue", "ok": True, "value": 100}],
    }
    summary = build_audit_summary(z3_results=z3, redhat_critiques=[], document=tree)
    doc = summary["document"]
    node = doc["body"][0]["children"][0]
    prov = (node.get("meta") or {}).get("provenance") or {}
    assert prov.get("source_name") == "NAIC_Underwriting_Policy_2025.pdf"
    assert str(prov.get("source_id")) == "sub-1"
    assert prov.get("page_number") in (4, "4")
    assert prov.get("excerpt")
    assert prov.get("rule")
    assert prov.get("verified_at")
