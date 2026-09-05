"""Import PDF bytes into a JDF AST using PyMuPDF."""

from __future__ import annotations

import base64
import re
from typing import Any

try:
    from ..models.jdf import JDFDocumentTree, empty_annotations, new_node_id
except ImportError:
    from models.jdf import JDFDocumentTree, empty_annotations, new_node_id


def _paragraph(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    return {
        "type": "paragraph",
        "id": new_node_id("p"),
        "content": cleaned,
        "entities_referenced": [],
        "provenance": [],
        "meta": {},
        "annotations": empty_annotations(),
    }


def _image_node(data: bytes, ext: str = "png", *, page: int, index: int) -> dict[str, Any]:
    mime = "image/png" if ext == "png" else f"image/{ext}"
    b64 = base64.b64encode(data).decode("ascii")
    return {
        "type": "image",
        "id": new_node_id("img"),
        "src": f"data:{mime};base64,{b64}",
        "alt": f"Page {page} figure {index}",
        "caption": "",
        "meta": {"page": page, "index": index},
        "annotations": empty_annotations(),
    }


def pdf_bytes_to_jdf(
    pdf_bytes: bytes,
    *,
    project_id: str,
    filename: str = "import.pdf",
) -> dict[str, Any]:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required for PDF import (pip install pymupdf).") from exc

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    body: list[dict[str, Any]] = []
    for page_index in range(len(doc)):
        page = doc.load_page(page_index)
        page_num = page_index + 1
        children: list[dict[str, Any]] = []
        blocks = page.get_text("blocks") or []
        for block in blocks:
            if len(block) < 5:
                continue
            text = str(block[4] or "").strip()
            if not text:
                continue
            for para in re.split(r"\n\s*\n", text):
                if para.strip():
                    children.append(_paragraph(para.strip()))
        image_idx = 0
        for img in page.get_images(full=True):
            xref = img[0]
            try:
                extracted = doc.extract_image(xref)
            except Exception:
                continue
            image_bytes = extracted.get("image") or b""
            if not image_bytes:
                continue
            image_idx += 1
            ext = str(extracted.get("ext") or "png").lower()
            children.append(_image_node(image_bytes, ext, page=page_num, index=image_idx))
        if not children:
            children.append(_paragraph(f"(Empty page {page_num})"))
        body.append(
            {
                "type": "section",
                "id": new_node_id("sec"),
                "title": f"Page {page_num}",
                "children": children,
                "meta": {"source_page": page_num},
                "annotations": empty_annotations(),
            }
        )
    title = re.sub(r"\.[^.]+$", "", filename.strip()) or f"Import {project_id}"
    tree = JDFDocumentTree(
        document_id=new_node_id("doc"),
        meta={"project_id": project_id, "title": title, "import_source": filename},
        truth_ledger={},
        body=body,
    )
    return tree.model_dump(mode="json")
