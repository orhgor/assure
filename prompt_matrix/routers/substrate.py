"""Substrate Vault upload routes — Textract single-page ingestion."""

from __future__ import annotations

import uuid
from pathlib import Path

from flask import jsonify, request

try:
    from ..db.jdf_repository import ensure_project
    from ..db.substrate_repository import save_substrate_entry
    from ..lib.logger import get_audit_logger
    from ..lib.textract import IMAGE_EXTENSIONS, TextractClient, TextractError
    from ..upload_limits import UploadRejectedError, validate_upload_bytes
except ImportError:
    from db.jdf_repository import ensure_project
    from db.substrate_repository import save_substrate_entry
    from lib.logger import get_audit_logger
    from lib.textract import IMAGE_EXTENSIONS, TextractClient, TextractError
    from upload_limits import UploadRejectedError, validate_upload_bytes

TEXTRACT_MAX_PAGES = 1


def register_substrate_routes(app) -> None:
    @app.post("/api/projects/<project_id>/substrate/upload")
    def substrate_upload(project_id: str):
        request_id = str(uuid.uuid4())
        audit = get_audit_logger()
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify({"ok": False, "error": "No file uploaded."}), 400

        filename = upload.filename.strip()
        file_bytes = upload.read()
        if not file_bytes:
            return jsonify({"ok": False, "error": "Empty file."}), 400

        try:
            validate_upload_bytes(filename, file_bytes)
        except UploadRejectedError as exc:
            return jsonify({"ok": False, "error": str(exc)}), exc.http_status

        client = TextractClient()
        try:
            page_count = client._get_page_count(file_bytes, filename)
        except TextractError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        if page_count > TEXTRACT_MAX_PAGES:
            audit.log_audit(
                request_id,
                project_id,
                "SUBSTRATE_UPLOAD",
                success=False,
                error_message=f"multi_page:{page_count}",
            )
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": (
                            f"This document has {page_count} pages. Substrate Vault accepts "
                            "single-page PDFs or images only. Split or export one page and try again."
                        ),
                        "page_count": page_count,
                        "max_pages": TEXTRACT_MAX_PAGES,
                    }
                ),
                400,
            )

        try:
            extracted = client.extract_text(file_bytes, filename)
        except TextractError as exc:
            audit.log_audit(
                request_id,
                project_id,
                "SUBSTRATE_UPLOAD",
                success=False,
                error_message=str(exc),
            )
            return jsonify({"ok": False, "error": str(exc)}), 503

        extracted_text = str(extracted.get("text") or "").strip()
        if len(extracted_text) <= 10:
            audit.log_audit(
                request_id,
                project_id,
                "SUBSTRATE_UPLOAD",
                success=False,
                error_message="extracted_text_too_short",
                details={"text_chars": len(extracted_text)},
            )
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": (
                            "Could not extract enough readable text from this file "
                            f"({len(extracted_text)} characters). Upload a clearer scan or "
                            "a file with more visible text."
                        ),
                        "text_chars": len(extracted_text),
                    }
                ),
                400,
            )

        ensure_project(project_id)
        entry = save_substrate_entry(
            project_id,
            filename=filename,
            page_count=extracted.get("page_count") or page_count,
            extracted_text=extracted_text,
            tables=extracted.get("tables") or [],
            forms=extracted.get("forms") or [],
        )

        audit.log_audit(
            request_id,
            project_id,
            "SUBSTRATE_UPLOAD",
            success=True,
            details={
                "filename": filename,
                "page_count": page_count,
                "table_count": len(entry.get("tables") or []),
                "text_chars": len(entry.get("extracted_text") or ""),
            },
        )

        return jsonify(
            {
                "ok": True,
                "id": entry["id"],
                "filename": filename,
                "page_count": page_count,
                "text": entry["extracted_text"],
                "tables": entry["tables"],
                "forms": entry["forms"],
                "is_image": Path(filename).suffix.lower() in IMAGE_EXTENSIONS,
            }
        )
