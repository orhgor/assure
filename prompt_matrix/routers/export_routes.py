"""Project document export routes (sync — runs in WSGI worker threadpool)."""

from __future__ import annotations

import re
import time
import uuid

from flask import Response, jsonify

try:
    from ..db.jdf_repository import fetch_latest_jdf_or_empty
    from ..exporters.docx_ast import export_jdf_to_docx
    from ..lib.logger import get_audit_logger
except ImportError:
    from db.jdf_repository import fetch_latest_jdf_or_empty
    from exporters.docx_ast import export_jdf_to_docx
    from lib.logger import get_audit_logger

_SAFE_NAME = re.compile(r"[^\w\-]+")


def _doc_title(tree: dict) -> str:
    meta = tree.get("meta") or {}
    raw = str(meta.get("title") or meta.get("project_id") or tree.get("document_id") or "assure-document")
    cleaned = _SAFE_NAME.sub("-", raw.strip()).strip("-") or "assure-document"
    return cleaned[:80]


def register_export_routes(app) -> None:
    @app.get("/api/projects/<project_id>/export")
    def export_project_document(project_id: str):
        """
        Sync endpoint: Gunicorn/Flask executes this in a worker thread.
        Keeps synchronous SQLite I/O and python-docx CPU work off concurrent SSE streams.
        """
        from flask import request

        request_id = str(uuid.uuid4())
        start_time = time.perf_counter()
        audit = get_audit_logger()
        fmt = (request.args.get("format") or "docx").strip().lower()
        if fmt != "docx":
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            audit.log_audit(
                request_id,
                project_id,
                "EXPORT_DOCX",
                success=False,
                duration_ms=duration_ms,
                error_message="Unsupported format",
                details={"format": fmt},
            )
            return jsonify({"error": "Unsupported format", "supported": ["docx"]}), 400

        try:
            tree = fetch_latest_jdf_or_empty(project_id)
            buffer = export_jdf_to_docx(tree)
            filename = f"{_doc_title(tree)}.docx"
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            audit.log_audit(
                request_id,
                project_id,
                "EXPORT_DOCX",
                success=True,
                duration_ms=duration_ms,
                details={"format": fmt, "filename": filename},
            )
            return Response(
                buffer.getvalue(),
                mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            audit.log_exception(
                request_id,
                project_id,
                "EXPORT_DOCX",
                exc,
                duration_ms=duration_ms,
                details={"format": fmt},
            )
            raise
