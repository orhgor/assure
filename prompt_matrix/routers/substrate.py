"""Substrate Vault upload routes — Textract ingestion (multi-page PDFs supported)."""

from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path

from flask import jsonify, request
from pydantic import BaseModel, Field
from werkzeug.utils import secure_filename

try:
    from ..db.jdf_repository import ensure_project, fetch_latest_jdf_or_empty
    from ..db.substrate_repository import (
        delete_substrate_entry,
        fetch_substrate_entry,
        list_substrate_for_project,
        save_substrate_entry,
        save_substrate_text,
        set_substrate_included,
        upsert_substrate_entry,
    )
    from ..lib.logger import get_audit_logger
    from ..lib.textract import IMAGE_EXTENSIONS, TextractClient, TextractError
    from ..models.jdf import flatten_nodes
    from ..middleware import project_ownership_required
    from ..services.compile_guard import flag_fields, flag_response
    from ..services.omp import (
        store_omp_artifact,
        build_omp_artifact_from_parse,
    )
    from ..services.omp_memory import remember_vault_file
    from ..services.verification import run_verification_after_parse
    from ..upload_limits import UploadRejectedError, validate_upload_bytes
except ImportError:
    from db.jdf_repository import ensure_project, fetch_latest_jdf_or_empty
    from db.substrate_repository import (
        delete_substrate_entry,
        fetch_substrate_entry,
        list_substrate_for_project,
        save_substrate_entry,
        save_substrate_text,
        set_substrate_included,
        upsert_substrate_entry,
    )
    from lib.logger import get_audit_logger
    from lib.textract import IMAGE_EXTENSIONS, TextractClient, TextractError
    from models.jdf import flatten_nodes
    from middleware import project_ownership_required
    from services.compile_guard import flag_fields, flag_response
    from services.omp import (
        store_omp_artifact,
        build_omp_artifact_from_parse,
    )
    from services.omp_memory import remember_vault_file
    from services.verification import run_verification_after_parse
    from upload_limits import UploadRejectedError, validate_upload_bytes

# Demo config: 200 pages for staging; production default stays at 50.
# The client demo shows real documents (policies, filings) that exceed 50.
TEXTRACT_MAX_PAGES = int(os.environ.get("ASSURE_MAX_PAGES", "50"))

log = logging.getLogger(__name__)

#: A document whose extraction carries no more than this many characters is a scan
#: with no text layer, not a source. The vault upload and the fetched-PDF path
#: (``services/web_retrieval``) refuse at the same floor.
MIN_EXTRACTED_TEXT_CHARS = 10


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


_TEXT_UPLOAD_SUFFIXES = {".txt", ".md"}


def _is_text_upload(filename: str) -> bool:
    """True for vault uploads that carry their own text (no extraction needed)."""
    return Path(filename).suffix.lower() in _TEXT_UPLOAD_SUFFIXES


def _temp_upload_dir() -> Path:
    override = (os.environ.get("TEMP_UPLOAD_DIR") or "").strip()
    if override:
        return Path(override)
    db_path = (os.environ.get("DATABASE_PATH") or "/app/data/history.sqlite").strip()
    return Path(db_path).parent / "tmp_uploads"


def _substrate_async_enabled() -> bool:
    return os.environ.get("SUBSTRATE_ASYNC_UPLOAD", "").lower() in ("1", "true", "yes")


def extract_document_text(filename: str, file_bytes: bytes) -> dict:
    """Text, tables, forms and page count of a document, by the upload path's own rules.

    Parser *selection* lives in ``services/parser_router.select_parser`` — this
    function executes the decision it returns: a text-like file wraps its
    content directly (no binary parsing, no probing), a clean PDF goes through
    the JDF CI bundle, and a scan (or a JDF failure) falls back best-effort to
    Docling/Textract — that fallback is execution, not a second router. The
    fetched-PDF path (``services/web_retrieval``) calls this too, so a PDF
    fetched from an allowlisted host is read by the same extractor an upload is.
    """
    from ..services.parser_router import _TEXT_LIKE_EXTENSIONS, select_parser

    use_docling = os.environ.get("USE_DOCLING", "0").lower() in ("1", "true", "yes")
    extracted: dict | None = None
    page_count = 1
    parser = select_parser(file_bytes, filename)

    def _suffix(filename: str) -> str:
        return Path(filename).suffix.lower().lstrip(".")

    if parser == "jdf" and (
        _is_text_upload(filename) or _suffix(filename) in _TEXT_LIKE_EXTENSIONS
    ):
        # Text-like file, routed here without any binary probe: the content is
        # already its own extracted form — wrap it directly (the "caller wraps
        # text as JDF" branch of the router contract). Textract and Docling
        # only read documents, so skip both rather than fail inside AWS.
        extracted = {
            "text": file_bytes.decode("utf-8", errors="replace"),
            "tables": [],
            "forms": [],
            "page_count": 1,
            "images": [],
            "figures": [],
            "parser_name": None,
            "source_kind": "text",
            "parse_confidence": None,
            "ocr_confidence": None,
            "table_count": 0,
            "image_count": 0,
            "figure_count": 0,
            "asset_summary": {"tables": 0, "images": 0, "figures": 0},
        }
    elif parser == "jdf":
        # JDF CI first — the default PDF parser path. The bundle carries the
        # text, the structured content (tables/images/figures as distinct
        # lists) and the parse/OCR confidence the vault row and the OMP
        # artifact both record. A converter failure falls back best-effort:
        # log it clearly, then let Docling/Textract take the document if it
        # can — never a text-only collapse while structured parsing works.
        from ..services.jdf_converter import JdfConversionError, pdf_to_parse_bundle

        try:
            bundle = pdf_to_parse_bundle(file_bytes, filename=filename, source_kind="pdf")
            extracted = {
                "text": bundle["text"],
                "tables": bundle["tables"],
                "images": bundle["images"],
                "figures": bundle["figures"],
                "forms": [],
                "page_count": bundle["page_count"],
                "parser_name": bundle["parser_name"],
                "source_kind": bundle["source_kind"],
                "parse_confidence": bundle["parse_confidence"],
                "ocr_confidence": bundle["ocr_confidence"],
                "table_count": bundle["table_count"],
                "image_count": bundle["image_count"],
                "figure_count": bundle["figure_count"],
                "asset_summary": bundle["asset_summary"],
            }
        except JdfConversionError as exc:
            log.warning("JDF CI parse failed for %s, falling back: %s", filename, exc)
    elif use_docling:
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
            images = parsed.get("images") or []
            figures = parsed.get("figures") or []
            extracted = {
                "text": str(parsed.get("full_text") or "").strip(),
                "tables": tables,
                "forms": [],
                "page_count": page_count,
                "images": images,
                "figures": figures,
                "parser_name": "docling",
                "source_kind": "pdf",
                "parse_confidence": parsed.get("parse_confidence"),
                "ocr_confidence": parsed.get("ocr_confidence"),
                "table_count": len(tables),
                "image_count": len(images),
                "figure_count": len(figures),
                "asset_summary": parsed.get("asset_summary"),
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
        # The Textract path has no structured-asset channel yet: empty lists,
        # unknown confidence — never fabricated scores or asset counts.
        extracted.setdefault("images", [])
        extracted.setdefault("figures", [])
        extracted.setdefault("parser_name", "textract")
        extracted.setdefault("source_kind", "pdf")
        extracted.setdefault("parse_confidence", None)
        extracted.setdefault("ocr_confidence", None)

    tables = extracted.get("tables") or []
    images = extracted.get("images") or []
    figures = extracted.get("figures") or []
    return {
        **extracted,
        "page_count": int(extracted.get("page_count") or page_count),
        "tables": tables,
        "images": images,
        "figures": figures,
        "table_count": (
            extracted.get("table_count")
            if extracted.get("table_count") is not None
            else len(tables)
        ),
        "image_count": (
            extracted.get("image_count")
            if extracted.get("image_count") is not None
            else len(images)
        ),
        "figure_count": (
            extracted.get("figure_count")
            if extracted.get("figure_count") is not None
            else len(figures)
        ),
        "asset_summary": extracted.get("asset_summary")
        or {"tables": len(tables), "images": len(images), "figures": len(figures)},
    }


def ingest_substrate_file(project_id: str, filename: str, file_bytes: bytes) -> dict:
    """Validate, extract, and persist a vault upload. Raises on validation/extraction errors."""
    validate_upload_bytes(filename, file_bytes)

    extracted = extract_document_text(filename, file_bytes)
    page_count = int(extracted.get("page_count") or 1)
    if page_count > TEXTRACT_MAX_PAGES:
        raise SubstrateIngestError(
            f"This document has {page_count} pages. Substrate Vault accepts up to "
            f"{TEXTRACT_MAX_PAGES} pages.",
            page_count=page_count,
        )

    extracted_text = str(extracted.get("text") or "").strip()
    if len(extracted_text) <= MIN_EXTRACTED_TEXT_CHARS:
        raise SubstrateIngestError(
            "Could not extract enough readable text from this file "
            f"({len(extracted_text)} characters). Upload a clearer scan or "
            "a file with more visible text.",
            text_chars=len(extracted_text),
        )

    ensure_project(project_id)
    flag = flag_fields(extracted_text)
    entry = upsert_substrate_entry(
        project_id,
        filename=filename,
        page_count=page_count,
        extracted_text=extracted_text,
        tables=extracted.get("tables") or [],
        forms=extracted.get("forms") or [],
        file_size_bytes=len(file_bytes),
        parser_name=extracted.get("parser_name"),
        source_kind=extracted.get("source_kind"),
        parse_confidence=extracted.get("parse_confidence"),
        ocr_confidence=extracted.get("ocr_confidence"),
        table_count=extracted.get("table_count"),
        image_count=extracted.get("image_count"),
        figure_count=extracted.get("figure_count"),
        asset_summary=extracted.get("asset_summary"),
        **flag,
    )
    remember_vault_file(project_id, str(entry["id"]), filename=filename, text=extracted_text)

    # Shared verification hook: Z3 + Red-Hat run here and only here
    # (services/verification). The vault ingest has no Assure tree of its
    # own, so the hook builds one from the extracted text's paragraphs and
    # attaches its result to the pseudo-bundle. Guarded: the ingest must
    # not fail on verification.
    try:
        paragraphs = [p for p in re.split(r"\n\s*\n", extracted_text) if p.strip()]
        verification_bundle = {
            "filename": filename,
            "page_count": page_count,
            "text": extracted_text,
            "chunks": [
                {"id": f"c{idx}", "text": para, "types": ["text"], "page": 1}
                for idx, para in enumerate(paragraphs)
            ],
        }
        verification = run_verification_after_parse(verification_bundle)
    except Exception:
        log.exception("post-parse verification failed; storing parse only")
        verification = None

    # Stage the parsed artifact into OMP immediately after the row write.
    # Confidence rides through explicitly — the OMP layer attaches it with
    # is-not-None guards, so a parser-reported 0.0 survives and unknown stays
    # None. Staging is best-effort, but a failure is never swallowed silently:
    # it is logged with the exception so an ingest whose artifact never
    # staged is diagnosable after the fact.
    omp_artifact_id = None
    try:
        substrate_result = {
            "id": entry["id"],
            "filename": filename,
            "page_count": page_count,
            "text": entry["extracted_text"],
            "tables": entry["tables"],
            "images": extracted.get("images") or [],
            "figures": extracted.get("figures") or [],
            "forms": entry.get("forms") or [],
            "size_bytes": entry.get("file_size_bytes", len(file_bytes)),
            "is_image": Path(filename).suffix.lower() in IMAGE_EXTENSIONS,
            "parser_name": extracted.get("parser_name"),
            "source_kind": extracted.get("source_kind"),
            "table_count": extracted.get("table_count"),
            "image_count": extracted.get("image_count"),
            "figure_count": extracted.get("figure_count"),
            "asset_summary": extracted.get("asset_summary"),
        }
        omp_artifact = build_omp_artifact_from_parse(
            project_id,
            substrate_result,
            parse_confidence=extracted.get("parse_confidence"),
            ocr_confidence=extracted.get("ocr_confidence"),
            parser_name=extracted.get("parser_name"),
            source_kind=extracted.get("source_kind"),
            page_count=page_count,
            table_count=extracted.get("table_count"),
            image_count=extracted.get("image_count"),
            figure_count=extracted.get("figure_count"),
            asset_summary=extracted.get("asset_summary"),
            verification=verification,
        )
        store_omp_artifact(project_id, omp_artifact)
        omp_artifact_id = omp_artifact.artifact_id
        # Link the row to its staged artifact.
        upsert_substrate_entry(
            project_id,
            filename=filename,
            page_count=page_count,
            extracted_text=extracted_text,
            tables=extracted.get("tables") or [],
            forms=extracted.get("forms") or [],
            file_size_bytes=len(file_bytes),
            parser_name=extracted.get("parser_name"),
            source_kind=extracted.get("source_kind"),
            parse_confidence=extracted.get("parse_confidence"),
            ocr_confidence=extracted.get("ocr_confidence"),
            table_count=extracted.get("table_count"),
            image_count=extracted.get("image_count"),
            figure_count=extracted.get("figure_count"),
            asset_summary=extracted.get("asset_summary"),
            omp_artifact_id=omp_artifact_id,
            **flag,
        )
    except Exception as exc:
        log.exception("Substrate ingest: OMP parse artifact staging failed: %s", exc)

    return {
        "ok": True,
        "id": entry["id"],
        "filename": filename,
        "page_count": page_count,
        "text": entry["extracted_text"],
        "tables": entry["tables"],
        "images": extracted.get("images") or [],
        "figures": extracted.get("figures") or [],
        "parser_name": extracted.get("parser_name"),
        "source_kind": extracted.get("source_kind"),
        "parse_confidence": extracted.get("parse_confidence"),
        "ocr_confidence": extracted.get("ocr_confidence"),
        "table_count": extracted.get("table_count"),
        "image_count": extracted.get("image_count"),
        "figure_count": extracted.get("figure_count"),
        "asset_summary": extracted.get("asset_summary"),
        "size_bytes": entry.get("file_size_bytes", len(file_bytes)),
        "is_image": Path(filename).suffix.lower() in IMAGE_EXTENSIONS,
        "omp_artifact_id": omp_artifact_id,
        **flag_response(flag),
    }


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
        filename = (payload.filename or "edge-upload.pdf").strip() or "edge-upload.pdf"
        flag = flag_fields(text)
        try:
            ensure_project(project_id)
            edge_row = save_substrate_text(
                project_id,
                text,
                page_count=payload.pageCount,
                source=payload.source or "edge",
            )
            save_substrate_entry(
                project_id,
                filename=filename,
                page_count=payload.pageCount,
                extracted_text=text,
                tables=[],
                forms=[],
                entry_id=edge_row["id"],
                **flag,
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

        remember_vault_file(project_id, str(edge_row["id"]), filename=filename, text=text)

        audit.log_audit(
            request_id,
            project_id,
            "SUBSTRATE_INGEST",
            success=True,
            details={
                "page_count": payload.pageCount,
                "text_chars": len(text),
                "source": payload.source or "edge",
                "instruction_like": flag["instruction_like"],
            },
        )
        return jsonify(
            {"ok": True, "id": edge_row["id"], "text_chars": len(text), **flag_response(flag)}
        )

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
                **flag_response(entry),
                "claims_count": claims_by_source_id.get(entry["id"], 0),
            }
            for entry in entries
        ]
        return jsonify({"ok": True, "files": files})

    @app.get("/api/projects/<project_id>/substrate/<file_id>")
    @project_ownership_required
    def substrate_get(project_id: str, file_id: str):
        entry = fetch_substrate_entry(project_id, file_id)
        if not entry:
            return jsonify({"ok": False, "error": "File not found."}), 404
        return jsonify(
            {
                "ok": True,
                "file": {
                    "id": entry.get("id"),
                    "filename": entry.get("filename"),
                    "page_count": entry.get("page_count"),
                    "extracted_text": entry.get("extracted_text") or "",
                    "included": entry.get("included", True),
                },
            }
        )

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
