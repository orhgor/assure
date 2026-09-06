"""Docling substrate extraction tests."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def sample_pdf(tmp_path):
    path = tmp_path / "policy.pdf"
    path.write_bytes(b"%PDF-1.4 sample")
    return path


def test_extract_substrate_document_tables_have_page_numbers(sample_pdf):
    mock_table = MagicMock()
    mock_table.export_to_markdown.return_value = "| Col | Val |\n| --- | --- |\n| A | 1 |"
    mock_table.prov = [MagicMock(page_no=3)]

    mock_doc = MagicMock()
    mock_doc.export_to_markdown.return_value = "# Policy\n\nTable below"
    mock_doc.tables = [mock_table]

    mock_result = MagicMock()
    mock_result.document = mock_doc

    mock_converter_mod = MagicMock()
    mock_converter_mod.DocumentConverter.return_value.convert.return_value = mock_result
    with patch.dict(
        sys.modules,
        {
            "docling": MagicMock(),
            "docling.document_converter": mock_converter_mod,
        },
    ):
        from prompt_matrix.verification.docling_extractor import extract_substrate_document

        payload = extract_substrate_document(str(sample_pdf))

    assert "Policy" in payload["full_text"]
    assert len(payload["extracted_tables"]) == 1
    assert payload["extracted_tables"][0]["provenance"]["page"] == 3
    assert payload["extracted_tables"][0]["node_type"] == "table"
