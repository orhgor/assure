"""Substrate Vault upload routes — Textract ingestion (multi-page PDFs supported)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from flask import jsonify, request
from pydantic import BaseModel, Field
from werkzeug.utils import secure_filename

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

TEXTRACT_MAX_PAGES = 50


class SubstrateIngestError(ValueError):
    """Validation/extraction failure with optional response fields."""

    def __init__(
        self,
        message: str,
        *,
        text_chars: int | None = None,
        page_count: int | None = None,
    ) -> None:
        super().__init__(message)
        self.text_chars = text_chars
        self.page_count = page_count


def _temp_upload_dir() -> Path:
    override = (os.environ.get("TEMP_UPLOAD_DIR") or "").strip()
    if override:
        return Path(override)
    db_path = (os.environ.get("DATABASE_PATH") or "/app/data/history.sqlite").strip()
    return Path(db_path).parent / "tmp_uploads"


def _substrate_async_enabled() -> bool:
    return os.environ.get("SUBSTRATE_ASYNC_UPLOAD", "").lower() in ("1", "true", "yes")


def ingest_substrate_file(project_id: str, filename: str, file_bytes: bytes) -> dict:
    """Validate, extract, and persist a vault upload. Raises on validation/extraction errors."""
    validate_upload_bytes(filename, file_bytes)

    use_docling = os.environ.get("USE_DOCLING", "0").lower() in ("1", "true", "yes")
    extracted: dict | None = None
    page_count = 1

    if use_docling:
        try:
            from ..verification.docling_extractor import extract_substrate_bytes

            temp_path = _temp_upload_dir() / f"{uuid.uuid4().hex}_{secure_filename(filename)}"
            parsed = extract_substrate_bytes(str(temp_path), file_bytes, filename)
            tables = parsed.get("extracted_tables") or []
            pages = [
                int((t.get("provenance") or {}).get("page") or 1)
                for t in tables
                if isinstance(t, dict)
            ]
            page_count = max(pages + [1])
            extracted = {
                "text": str(parsed.get("full_text") or "").strip(),
                "tables": tables,
                "forms": [],
                "page_count": page_count,
            }
        except Exception:
            extracted = None

    if extracted is None:
        client = TextractClient()
        page_count = client._get_page_count(file_bytes, filename)
        if page_count > TEXTRACT_MAX_PAGES:
            raise SubstrateIngestError(
                f"This document has {page_count} pages. Substrate Vault accepts up to "
                f"{TEXTRACT_MAX_PAGES} pages.",
                page_count=page_count,
            )
        extracted = client.extract_text(file_bytes, filename)
        page_count = int(extracted.get("page_count") or page_count)

    if page_count > TEXTRACT_MAX_PAGES:
        raise SubstrateIngestError(
            f"This document has {page_count} pages. Substrate Vault accepts up to "
            f"{TEXTRACT_MAX_PAGES} pages.",
            page_count=page_count,
        )

    extracted_text = str(extracted.get("text") or "").strip()
    if len(extracted_text) <= 10:
        raise SubstrateIngestError(
            "Could not extract enough readable text from this file "
            f"({len(extracted_text)} characters). Upload a clearer scan or "
            "a file with more visible text.",
            text_chars=len(extracted_text),
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
    return {
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

        if _substrate_async_enabled():
            temp_dir = _temp_upload_dir()
            temp_dir.mkdir(parents=True, exist_ok=True)
            safe_name = secure_filename(filename) or "upload.bin"
            temp_path = temp_dir / f"{uuid.uuid4().hex}_{safe_name}"
            temp_path.write_bytes(file_bytes)
            try:
                from ..tasks.substrate_tasks import process_substrate_upload
            except ImportError:
                from tasks.substrate_tasks import process_substrate_upload
            task = process_substrate_upload.apply_async(
                args=[project_id, str(temp_path), filename],
                queue="sqlite_writes",
            )
            audit.log_audit(
                request_id,
                project_id,
                "SUBSTRATE_UPLOAD",
                success=True,
                details={"filename": filename, "async": True, "task_id": task.id},
            )
            return jsonify({"ok": True, "task_id": task.id, "status": "queued"}), 202

        try:
            payload = ingest_substrate_file(project_id, filename, file_bytes)
        except SubstrateIngestError as exc:
            audit.log_audit(
                request_id,
                project_id,
                "SUBSTRATE_UPLOAD",
                success=False,
                error_message=str(exc),
            )
            body: dict = {"ok": False, "error": str(exc)}
            if exc.text_chars is not None:
                body["text_chars"] = exc.text_chars
            if exc.page_count is not None:
                body["page_count"] = exc.page_count
                body["max_pages"] = TEXTRACT_MAX_PAGES
            return jsonify(body), 400
        except TextractError as exc:
            audit.log_audit(
                request_id,
                project_id,
                "SUBSTRATE_UPLOAD",
                success=False,
                error_message=str(exc),
            )
            return jsonify({"ok": False, "error": str(exc)}), 503

        audit.log_audit(
            request_id,
            project_id,
            "SUBSTRATE_UPLOAD",
            success=True,
            details={
                "filename": filename,
                "page_count": payload.get("page_count"),
                "text_chars": len(payload.get("text") or ""),
            },
        )
        return jsonify(payload)

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
