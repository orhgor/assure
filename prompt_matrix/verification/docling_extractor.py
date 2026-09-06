"""Docling-based substrate extraction (tables + markdown layout)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def extract_substrate_document(file_path: str) -> dict[str, Any]:
    """
    Parse insurance policies and loss runs. Extract text, charts,
    and highly accurate markdown tables using Docling layout analysis.
    """
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise RuntimeError("docling is not installed") from exc

    converter = DocumentConverter()
    result = converter.convert(Path(file_path))
    doc = result.document
    full_md = doc.export_to_markdown()
    jdf_tables: list[dict[str, Any]] = []
    for table in doc.tables:
        table_md = table.export_to_markdown(doc)
        page_no = table.prov[0].page_no if table.prov else None
        jdf_tables.append(
            {
                "node_type": "table",
                "provenance": {"source_file": os.path.basename(file_path), "page": page_no},
                "content_md": table_md,
            }
        )
    return {"full_text": full_md, "extracted_tables": jdf_tables}


def extract_substrate_bytes(file_path: str, file_bytes: bytes, filename: str) -> dict[str, Any]:
    """Write bytes to path and run Docling extraction."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(file_bytes)
    try:
        return extract_substrate_document(str(path))
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
