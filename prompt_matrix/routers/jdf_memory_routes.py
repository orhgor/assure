"""JDF memory (knowledge-base) ingest + search endpoints, project-scoped."""
from __future__ import annotations

import logging
import os
import shutil

from flask import jsonify, request

try:
    from ..middleware import project_ownership_required
except ImportError:
    from middleware import project_ownership_required

try:
    from ..db.jdf_repository import ensure_project
    from ..db.substrate_repository import upsert_substrate_entry
    from ..services.jdf_converter import JDF_BIN, JdfConversionError
    from ..services.jdf_converter import chunks_to_text, jdf_to_chunks, pdf_to_jdf
    from ..services.jdf_memory import OmpUnavailable, remember_jdf_document, search_jdf_chunks
    from ..services.omp_memory import remember_vault_file
except ImportError:
    from db.jdf_repository import ensure_project
    from db.substrate_repository import upsert_substrate_entry
    from services.jdf_converter import JDF_BIN, JdfConversionError
    from services.jdf_converter import chunks_to_text, jdf_to_chunks, pdf_to_jdf
    from services.jdf_memory import OmpUnavailable, remember_jdf_document, search_jdf_chunks
    from services.omp_memory import remember_vault_file

log = logging.getLogger(__name__)

_MAX_PDF_BYTES = 25 * 1024 * 1024


def _jdf_page_count(jdf_dict: dict) -> int:
    pages = jdf_dict.get("pages")
    return len(pages) if isinstance(pages, list) and pages else 1


def _store_grounding_source(
    project_id: str,
    filename: str,
    chunks: list[dict],
    *,
    size_bytes: int,
    page_count: int,
) -> None:
    """Leave the ingested PDF as a compile source for this project.

    A compile grounds only through POST /draft's substrate_file_ids, and
    fetch_substrate_entries_by_ids reads substrate_vault, so an ingest that
    indexed chunks without a vault row left the document ungrounded: the source
    panel read "0 sources", the draft carried substrate_file_ids: [] and Red-Hat
    skipped with "no substrate or empty draft". The text is already extracted
    here (the chunks are the document), so no Textract/Docling pass is involved
    and the vault upload route keeps its .txt/.md restriction untouched.
    """
    text = chunks_to_text(chunks)
    if not text:
        return
    ensure_project(project_id)
    entry = upsert_substrate_entry(
        project_id,
        filename=filename,
        page_count=page_count,
        extracted_text=text,
        file_size_bytes=size_bytes,
    )
    remember_vault_file(
        project_id,
        str(entry["id"]),
        filename=filename,
        text=text,
    )


def register_jdf_memory_routes(app) -> None:
    @app.post("/api/projects/<project_id>/jdf/ingest")
    @project_ownership_required
    def jdf_ingest(project_id: str):
        # FIX 3: size cap before reading body bytes.
        content_length = request.content_length or 0
        if content_length > _MAX_PDF_BYTES:
            return jsonify({"error": "file too large (max 25MB)"}), 413
        if "file" not in request.files:
            return jsonify({"error": "no file"}), 400
        f = request.files["file"]
        if not f.filename.lower().endswith(".pdf"):
            return jsonify({"error": "only PDF supported in MVP"}), 400
        doc_id = request.form.get("doc_id") or f.filename
        try:
            pdf_bytes = f.read()
            jdf_dict = pdf_to_jdf(pdf_bytes)
            chunks = jdf_to_chunks(jdf_dict, strategy="section")
            # Before the chunk index: a compile grounds from the project's
            # substrate_file_ids, so the vault row is what makes this ingest
            # visible to the source panel and to the draft pipeline.
            _store_grounding_source(
                project_id,
                f.filename,
                chunks,
                size_bytes=len(pdf_bytes),
                page_count=_jdf_page_count(jdf_dict),
            )
            result = remember_jdf_document(doc_id, jdf_dict, chunks, tenant_id=project_id)
            return jsonify({"ok": True, **result})
        except OmpUnavailable:  # FIX 4
            return jsonify({"error": "index temporarily unavailable, try again"}), 503
        except JdfConversionError as e:
            log.exception("jdf ingest failed")
            return jsonify({"error": str(e)}), 500

    @app.post("/api/projects/<project_id>/jdf/search")
    @project_ownership_required
    def jdf_search(project_id: str):
        body = request.get_json(silent=True) or {}
        q = (body.get("query") or "").strip()
        if not q:
            return jsonify({"error": "query required"}), 400
        try:
            limit = int(body.get("limit", 20))
        except (TypeError, ValueError):
            limit = 20
        results = search_jdf_chunks(q, tenant_id=project_id, limit=limit)
        return jsonify({"ok": True, "project_id": project_id, "count": len(results), "results": results})

    @app.get("/api/projects/<project_id>/jdf/health")
    @project_ownership_required
    def jdf_health(project_id: str):
        ok = bool(JDF_BIN and (os.path.exists(JDF_BIN) or shutil.which("jdf")))
        return jsonify({"ok": ok, "project_id": project_id, "jdf_bin": JDF_BIN}), (200 if ok else 503)