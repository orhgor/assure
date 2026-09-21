"""JDF annotation and document tree tests."""

from __future__ import annotations

from prompt_matrix.models.jdf import (
    attach_redhat_annotation,
    attach_z3_annotation,
    build_document_from_draft,
    draft_text_to_sections,
    parse_document,
)


def test_draft_text_to_sections():
    body = draft_text_to_sections("## Revenue\n\nQ3 ARR reached $12M.")
    assert len(body) == 1
    assert body[0]["title"] == "Revenue"
    assert body[0]["children"][0]["annotations"]["redhat"] == []


def test_build_document_from_draft_validates():
    doc = build_document_from_draft("default", "Hello world.")
    parsed = parse_document(doc.model_dump(mode="json"))
    assert parsed.document_id == "doc-default"
    assert len(parsed.body) == 1


def test_redhat_annotation_not_sibling_node():
    tree = build_document_from_draft("p1", "Paragraph with claim.").model_dump(mode="json")
    para_id = tree["body"][0]["children"][0]["id"]
    updated, ok = attach_redhat_annotation(tree, para_id, "Unsupported claim.")
    assert ok
    child = updated["body"][0]["children"][0]
    assert child["type"] == "paragraph"
    assert len(child["annotations"]["redhat"]) == 1
    assert "Unsupported" in child["annotations"]["redhat"][0]["text"]
    assert not any(c.get("type") == "callout" for c in updated["body"][0]["children"])


def test_z3_annotation_on_node():
    tree = build_document_from_draft("p1", "Revenue is $10M.").model_dump(mode="json")
    para_id = tree["body"][0]["children"][0]["id"]
    updated, ok = attach_z3_annotation(
        tree, para_id, "Metric contradicts ledger", canonical_key="Revenue", status="violation"
    )
    assert ok
    z3 = updated["body"][0]["children"][0]["annotations"]["z3"]
    assert len(z3) == 1
    assert z3[0]["canonical_key"] == "Revenue"


def test_malformed_jdf_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        parse_document({"document_id": "x", "body": [{"type": "paragraph", "content": "no id"}]})


def test_parse_document_strips_unknown_keys():
    from prompt_matrix.models.jdf import parse_document

    parsed = parse_document(
        {
            "document_id": "doc-x",
            "type": "document",
            "title": "Root",
            "meta": {},
            "truth_ledger": {},
            "body": [
                {
                    "type": "section",
                    "id": "s1",
                    "title": "S",
                    "confidence": 0.9,
                    "children": [
                        {
                            "type": "paragraph",
                            "id": "p1",
                            "content": "Hello.",
                            "gutter": "verified",
                            "entities_referenced": [],
                            "provenance": [],
                            "meta": {},
                            "annotations": {"redhat": [], "z3": []},
                        }
                    ],
                    "meta": {},
                    "annotations": {"redhat": [], "z3": []},
                }
            ],
        }
    )
    dumped = parsed.model_dump(mode="json")
    assert dumped["body"][0]["children"][0]["content"] == "Hello."
    assert "gutter" not in dumped["body"][0]["children"][0]
    assert "confidence" not in dumped["body"][0]
    assert (dumped.get("meta") or {}).get("title") == "Root"


def test_unknown_leaf_key_ignored_on_model():
    from prompt_matrix.models.jdf import JDFParagraphNode

    node = JDFParagraphNode.model_validate(
        {"type": "paragraph", "id": "p-1", "content": "Hello", "gutter": "verified"}
    )
    assert node.content == "Hello"
    assert not hasattr(node, "gutter") or "gutter" not in node.model_dump()
