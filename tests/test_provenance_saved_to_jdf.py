"""Provenance meta persisted on JDF nodes after audit summary."""

from __future__ import annotations

from prompt_matrix.models.jdf import empty_annotations, parse_document
from prompt_matrix.routers.draft import attach_citations_to_tree
from prompt_matrix.routers.jdf_routes import _serve_citation_rows
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


def _cited_tree() -> dict:
    """A compiled tree whose paragraph cites a numbered source sentence.

    Driven through the real writer, ``attach_citations_to_tree``, not a hand-built
    row, so the shape under test is the shape a compile persists: the cited row's
    ``page`` is the substrate row's ``page_number``, which every ingest writes as
    an ``int`` (``substrate_vault.page_number``).
    """
    tree = {
        "document_id": "doc-served",
        "meta": {},
        "truth_ledger": {},
        "body": [
            {
                "type": "section",
                "id": "sec-1",
                "title": "Renewal",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "para-1",
                        "content": (
                            "The renewal policy has a minimum earned premium of "
                            "35 percent of the total premium. [S1]"
                        ),
                        "meta": {},
                        "annotations": empty_annotations(),
                    }
                ],
                "meta": {},
                "annotations": empty_annotations(),
            }
        ],
    }
    return attach_citations_to_tree(
        tree,
        [
            {
                "id": "sub-1",
                "filename": "brim-cp-media43.pdf",
                "page_number": 4,
                "extracted_text": (
                    "The renewal policy has a minimum earned premium of 35 percent "
                    "of the total premium."
                ),
            }
        ],
    )


def test_served_document_parses_back_into_the_model_it_came_from() -> None:
    """GET /jdf must serve a document ``parse_document`` accepts.

    The read path folds a cited row's ``page`` into a blank ``page_number`` for the
    Evidence pane. It folded an ``int`` into a field ``JDFProvenance`` declares as
    ``str``, so the served document was rejected by the model it was serialised
    from — and every route that takes the shell's own document back (the audit,
    the rewrite) refused it before any model was called. Measured on the demo
    project: 180 validation errors, one per provenance row.
    """
    compiled = _cited_tree()
    row = compiled["body"][0]["children"][0]["provenance"][0]
    assert row["page"] == 4
    assert not row.get("page_number")
    parse_document(compiled)

    served = _serve_citation_rows(compiled)
    served_row = served["body"][0]["children"][0]["provenance"][0]
    parse_document(served)
    assert served_row["page_number"] == "4"
