"""Substrate Vault upload routes — Textract single-page ingestion + edge ingest."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from flask import jsonify, request
from pydantic import BaseModel, Field

try:
    from ..db.jdf_repository import ensure_project, fetch_latest_jdf_or_empty
    from ..db.substrate_repository import (
        delete_substrate_entry,
        list_substrate_for_project,
        save_substrate_entry,
        save_substrate_text,
        set_substrate_included,
    )
    from ..lib.logger import get_audit_logger
    from ..lib.textract import IMAGE_EXTENSIONS, TextractClient, TextractError
    from ..models.jdf import flatten_nodes
    from ..middleware import project_ownership_required
    from ..services.omp_memory import remember_vault_file
    from ..upload_limits import UploadRejectedError, validate_upload_bytes
except ImportError:
    from db.jdf_repository import ensure_project, fetch_latest_jdf_or_empty
    from db.substrate_repository import (
        delete_substrate_entry,
        list_substrate_for_project,
        save_substrate_entry,
        save_substrate_text,
        set_substrate_included,
    )
    from lib.logger import get_audit_logger
    from lib.textract import IMAGE_EXTENSIONS, TextractClient, TextractError
    from models.jdf import flatten_nodes
    from middleware import project_ownership_required
    from services.omp_memory import remember_vault_file
    from upload_limits import UploadRejectedError, validate_upload_bytes

TEXTRACT_MAX_PAGES = 1


def _index_vault_file(project_id: str, entry: dict) -> None:
    try:
        remember_vault_file(
            project_id,
            str(entry.get("id") or ""),
            filename=str(entry.get("filename") or ""),
            text=str(entry.get("extracted_text") or ""),
        )
    except Exception:
        pass


class SubstrateIngestPayload(BaseModel):
    projectId: str = Field(min_length=1)
    text: str = Field(min_length=1)
    pageCount: int = Field(default=1, ge=1, le=50)
    filename: str | None = None
    source: str = "edge"


def _substrate_ingest_secret() -> str:
    return (os.environ.get("SUBSTRATE_INGEST_SECRET") or "").strip()


def _authorize_worker_ingest() -> bool:
    secret = _substrate_ingest_secret()
    if not secret:
        return True
    header = (request.headers.get("X-Assure-Worker-Secret") or "").strip()
    auth = (request.headers.get("Authorization") or "").strip()
    if header == secret:
        return True
    if auth == f"Bearer {secret}":
        return True
    return False


def register_substrate_routes(app) -> None:
    try:
        from ..rate_limits import limiter, worker_ingest_request
    except ImportError:
        from rate_limits import limiter, worker_ingest_request

    @app.post("/api/substrate")
    @limiter.limit("60 per minute", exempt_when=worker_ingest_request)
    def substrate_ingest():
        request_id = str(uuid.uuid4())
        audit = get_audit_logger()
        if not _authorize_worker_ingest():
            return jsonify({"ok": False, "error": "Unauthorized."}), 401

        data = request.get_json(silent=True) or {}
        try:
            payload = SubstrateIngestPayload.model_validate(data)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        text = payload.text.strip()
        if len(text) <= 10:
            return (
                jsonify({"ok": False, "error": "Extracted text is too short to ingest."}),
                400,
            )

        project_id = payload.projectId.strip()
        try:
            ensure_project(project_id)
            edge_row = save_substrate_text(
                project_id,
                text,
                page_count=payload.pageCount,
                source=payload.source or "edge",
            )
            filename = (payload.filename or "edge-upload.pdf").strip() or "edge-upload.pdf"
            save_substrate_entry(
                project_id,
                filename=filename,
                page_count=payload.pageCount,
                extracted_text=text,
                tables=[],
                forms=[],
                entry_id=edge_row["id"],
            )
        except Exception as exc:
            audit.log_audit(
                request_id,
                project_id,
                "SUBSTRATE_INGEST",
                success=False,
                error_message=str(exc),
            )
            return jsonify({"ok": False, "error": "Database write failed."}), 500

        _index_vault_file(project_id, {**edge_row, "filename": filename, "extracted_text": text})

        audit.log_audit(
            request_id,
            project_id,
            "SUBSTRATE_INGEST",
            success=True,
            details={
                "page_count": payload.pageCount,
                "text_chars": len(text),
                "source": payload.source or "edge",
            },
        )
        return jsonify({"ok": True, "id": edge_row["id"], "text_chars": len(text)})

    @app.post("/api/projects/<project_id>/substrate/upload")
    @project_ownership_required
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
            file_size_bytes=len(file_bytes),
        )
        _index_vault_file(project_id, entry)

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
                "size_bytes": entry.get("file_size_bytes", len(file_bytes)),
                "is_image": Path(filename).suffix.lower() in IMAGE_EXTENSIONS,
            }
        )

    @app.get("/api/projects/<project_id>/substrate")
    @project_ownership_required
    def substrate_list(project_id: str):
        entries = list_substrate_for_project(project_id)
        if not entries:
            return jsonify({"ok": True, "files": []})

        doc = fetch_latest_jdf_or_empty(project_id)
        claims_by_source_id: dict[str, int] = {}
        for node in flatten_nodes(doc):
            for prov in node.get("provenance") or []:
                if not isinstance(prov, dict):
                    continue
                source_id = str(prov.get("source_id") or "").strip()
                if source_id:
                    claims_by_source_id[source_id] = claims_by_source_id.get(source_id, 0) + 1

        files = [
            {
                **entry,
                "claims_count": claims_by_source_id.get(entry["id"], 0),
            }
            for entry in entries
        ]
        return jsonify({"ok": True, "files": files})

    @app.delete("/api/projects/<project_id>/substrate/<file_id>")
    @project_ownership_required
    def substrate_delete(project_id: str, file_id: str):
        request_id = str(uuid.uuid4())
        audit = get_audit_logger()
        removed = delete_substrate_entry(project_id, file_id)
        if not removed:
            return jsonify({"ok": False, "error": "File not found."}), 404
        audit.log_audit(
            request_id,
            project_id,
            "SUBSTRATE_DELETE",
            success=True,
            details={"file_id": file_id},
        )
        return jsonify({"ok": True, "id": file_id})

    @app.patch("/api/projects/<project_id>/substrate/<file_id>")
    @project_ownership_required
    def substrate_patch(project_id: str, file_id: str):
        data = request.get_json(silent=True) or {}
        if "included" not in data:
            return jsonify({"ok": False, "error": "Missing 'included' field."}), 400
        updated = set_substrate_included(project_id, file_id, bool(data.get("included")))
        if not updated:
            return jsonify({"ok": False, "error": "File not found."}), 404
        return jsonify({"ok": True, "id": file_id, "included": bool(data.get("included"))})
