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
    from ..services.jdf_converter import JDF_BIN, JdfConversionError
    from ..services.jdf_converter import jdf_to_chunks, pdf_to_jdf
    from ..services.jdf_memory import OmpUnavailable, remember_jdf_document, search_jdf_chunks
except ImportError:
    from services.jdf_converter import JDF_BIN, JdfConversionError
    from services.jdf_converter import jdf_to_chunks, pdf_to_jdf
    from services.jdf_memory import OmpUnavailable, remember_jdf_document, search_jdf_chunks

log = logging.getLogger(__name__)

_MAX_PDF_BYTES = 25 * 1024 * 1024


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
            jdf_dict = pdf_to_jdf(f.read())
            chunks = jdf_to_chunks(jdf_dict, strategy="section")
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