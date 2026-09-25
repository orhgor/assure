"""JDF persistence REST endpoints with positional docking."""

from __future__ import annotations

import logging
import mimetypes
import re
import time
import uuid
from typing import Any

from flask import jsonify, request
from pydantic import BaseModel, ConfigDict

log = logging.getLogger(__name__)

try:
    from ..db.document_lock_repository import is_version_locked
    from ..db.drafts_repository import upsert_draft
    from ..db.ingest_jobs_repository import create_job, set_task
    from ..db.jdf_repository import (
        RevisionConflict,
        current_document_version,
        ensure_project,
        fetch_jdf_at_version,
        fetch_latest_jdf_or_empty,
        get_omp_linkages_for_revision,
        list_jdf_revisions,
        patch_jdf_node,
        save_jdf_revision,
        save_omp_linkage,
    )
    from ..db.node_revision_repository import (
        fetch_node_revision,
        list_node_revisions,
    )
    from ..lib.logger import get_audit_logger
    from ..lib.sanitize import sanitize_jdf_node
    from ..middleware import project_ownership_required
    from ..models.jdf import (
        insert_node_after_anchor,
        parse_document,
        splice_node,
    )
    from ..services.jdf_converter import (
        JdfConversionError,
        jdf_to_document_tree,
        ocr_engine,
        pdf_to_parse_bundle,
    )
    from ..services.jdf_sidecar import audit_jdf_payload, sidecar_document
    from ..services.object_store import get_object_store, key_belongs_to_project, upload_key
    from ..services.omp import build_omp_artifact_from_parse, store_omp_artifact
    from ..services.parser_router import select_parser
    from ..services.pdf_import import pdf_bytes_to_jdf
    from ..services.pdf_ingest import PdfIngestError, ingest_pdf_for_project, parse_async_enabled
    from ..services.verification import run_verification_after_parse
    from ..upload_limits import UploadRejectedError, max_upload_bytes, validate_upload_bytes
except ImportError:
    from db.document_lock_repository import is_version_locked
    from db.drafts_repository import upsert_draft
    from db.ingest_jobs_repository import create_job, set_task
    from db.jdf_repository import (
        RevisionConflict,
        current_document_version,
        ensure_project,
        fetch_jdf_at_version,
        fetch_latest_jdf_or_empty,
        get_omp_linkages_for_revision,
        list_jdf_revisions,
        patch_jdf_node,
        save_jdf_revision,
        save_omp_linkage,
    )
    from db.node_revision_repository import (
        fetch_node_revision,
        list_node_revisions,
    )
    from lib.logger import get_audit_logger
    from lib.sanitize import sanitize_jdf_node
    from middleware import project_ownership_required
    from models.jdf import (
        insert_node_after_anchor,
        parse_document,
        splice_node,
    )
    from services.jdf_converter import (
        JdfConversionError,
        jdf_to_document_tree,
        ocr_engine,
        pdf_to_parse_bundle,
    )
    from services.jdf_sidecar import audit_jdf_payload, sidecar_document
    from services.object_store import get_object_store, key_belongs_to_project, upload_key
    from services.omp import build_omp_artifact_from_parse, store_omp_artifact
    from services.parser_router import select_parser
    from services.pdf_import import pdf_bytes_to_jdf
    from services.pdf_ingest import PdfIngestError, ingest_pdf_for_project, parse_async_enabled
    from services.verification import run_verification_after_parse
    from upload_limits import UploadRejectedError, max_upload_bytes, validate_upload_bytes


class RestoreJDFPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: int
    workspace_id: str | None = None


class SaveJDFPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document: dict[str, Any] | None = None
    title: str | None = None
    mutation_type: str = "NODE_DOCK"
    target_node_id: str | None = None
    insert_after_id: str | None = None
    new_node: dict[str, Any] | None = None
    change_summary: str | None = None
    id: str | None = None
    node_data: dict[str, Any] | None = None
    expected_version: int | None = None


def _incoming_document(payload: SaveJDFPayload, project_id: str) -> dict[str, Any]:
    raw = dict(payload.document or {})
    raw.pop("type", None)
    root_title = raw.pop("title", None)
    if not str(raw.get("document_id") or "").strip():
        raw["document_id"] = f"doc-{project_id}"
    meta = dict(raw.get("meta") or {})
    chosen = (payload.title or root_title or "").strip()
    if chosen:
        meta["title"] = chosen
    raw["meta"] = meta
    raw.setdefault("truth_ledger", {})
    raw.setdefault("body", [])
    return raw


def _conflict_payload(exc: RevisionConflict):
    return (
        jsonify(
            {
                "ok": False,
                "error": "Conflict: node was modified elsewhere",
                "latest_version": exc.latest_version,
                "current_content": exc.current_content,
            }
        ),
        409,
    )


def _resolve_tree(payload: SaveJDFPayload, project_id: str) -> dict[str, Any]:
    if payload.document is not None:
        tree = parse_document(_incoming_document(payload, project_id)).model_dump(mode="json")
    else:
        tree = fetch_latest_jdf_or_empty(project_id)
        chosen = (payload.title or "").strip()
        if chosen:
            meta = dict(tree.get("meta") or {})
            meta["title"] = chosen
            tree["meta"] = meta

    if payload.new_node and payload.insert_after_id:
        tree, _ = insert_node_after_anchor(tree, payload.insert_after_id, payload.new_node)
    elif payload.new_node and payload.target_node_id:
        tree, _ = splice_node(tree, payload.target_node_id, payload.new_node)
    elif payload.document is None and not payload.new_node:
        raise ValueError("document or new_node required")

    return tree


def _serve_citation_rows(document: dict[str, Any]) -> dict[str, Any]:
    """Serve every provenance row with its page under ``page_number``.

    A cited row is stamped ``{extracted_quote, source_name, page, cited_id}``
    (``routers/draft.py:attach_citations_to_tree``) — the page under the name the
    substrate rows use — while the matcher's rows and every reader on this side
    (the Evidence pane, the JDF canvas, the source list, the .docx export) read
    ``page_number``. ``models.jdf.JDFProvenance`` keeps both names, so a served
    row may carry either, and a reader would otherwise have to know the alias.
    The read path settles it once, here, in place on the document this response is
    built from: ``fetch_latest_jdf_or_empty`` decodes a fresh copy per call and
    nothing is written back to SQLite.

    The fold converts to ``page_number``'s declared type — a ``str`` — because the
    two names do not agree on one: ``page`` is the substrate row's ``int``
    (``models.jdf.JDFProvenance.page``) while ``page_number`` is the matcher's
    ``str``. Serving the int verbatim made this response unparseable by the model
    it was serialised from, so every route that takes the shell's own document
    back refused it before a model was called — measured on the demo project:
    ``parse_document`` raised 180 validation errors (one per provenance row) and
    the room's own "Run Red-Hat" answered ``Invalid document: 180 validation
    errors``. A display fold must not cross a type boundary: whatever this
    function serves has to round-trip through ``parse_document``.
    """
    for section in document.get("body") or []:
        if not isinstance(section, dict):
            continue
        for node in (section, *(section.get("children") or [])):
            if not isinstance(node, dict):
                continue
            for row in node.get("provenance") or []:
                if not isinstance(row, dict):
                    continue
                if row.get("page_number") in (None, ""):
                    page = row.get("page")
                    if page not in (None, ""):
                        row["page_number"] = str(page)
    return document
#: What ``uploads/presign`` and ``import-pdf`` accept: PDFs, the raster
#: formats the router sends to OCR (``parser_router._IMAGE_EXTENSIONS``) and
#: the text types ``upload_limits.validate_upload_bytes`` admits. One set here
#: so the presign answer and the multipart validation agree.
_UPLOAD_EXTENSIONS = frozenset(
    {"pdf", "png", "jpg", "jpeg", "tif", "tiff", "bmp", "txt", "md"}
)
_UPLOAD_CONTENT_TYPE_PREFIXES = ("application/pdf", "image/", "text/", "application/octet-stream")


def upload_extension_allowed(filename: str) -> bool:
    name = (filename or "").lower()
    return "." in name and name.rsplit(".", 1)[-1] in _UPLOAD_EXTENSIONS


def upload_content_type_allowed(content_type: str) -> bool:
    return (content_type or "").lower().startswith(_UPLOAD_CONTENT_TYPE_PREFIXES)


def guess_upload_content_type(filename: str) -> str:
    """The object's stored content type follows the file, not a PDF constant."""
    return mimetypes.guess_type(filename or "")[0] or "application/octet-stream"


def _textract_parse_bundle(file_bytes: bytes, filename: str) -> dict[str, Any]:
    """Parse-bundle shape for the router's "textract" decision.

    The router decided this document is a scan (no text layer), so Textract
    reads it. The result is shaped exactly like a ``pdf_to_parse_bundle`` so
    the route's downstream tree-building and OMP staging need no second code
    path: chunks carry one paragraph each so ``jdf_to_document_tree`` renders
    the text as real body paragraphs. Parse/OCR confidence is None — Textract
    reports no confidence figure we trust, and None is the honest unknown.
    """
    try:
        from ..lib.textract import TextractClient
    except ImportError:  # pragma: no cover - flat-import fallback
        from lib.textract import TextractClient

    extracted = TextractClient().extract_text(file_bytes, filename)
    text = str(extracted.get("text") or "").strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks = [
        {"id": f"c{idx}", "text": para, "types": ["text"], "page": 1}
        for idx, para in enumerate(paragraphs)
    ]
    page_count = int(extracted.get("page_count") or 1)
    return {
        "jdf": {"$jdf": "1.0", "meta": {}, "pages": [{} for _ in range(page_count)]},
        "chunks": chunks,
        "text": text,
        "page_count": page_count,
        "parser_name": "textract",
        "source_kind": "pdf",
        "parse_confidence": None,
        "ocr_confidence": None,
        "tables": extracted.get("tables") or [],
        "images": [],
        "figures": [],
        "table_count": len(extracted.get("tables") or []),
        "image_count": 0,
        "figure_count": 0,
        "asset_summary": {
            "tables": len(extracted.get("tables") or []),
            "images": 0,
            "figures": 0,
        },
        "filename": filename,
    }


def register_jdf_routes(app) -> None:
    @app.get("/api/projects/<project_id>/history")
    @project_ownership_required
    def get_project_history(project_id: str):
        revisions = list_jdf_revisions(project_id)
        history = [
            {
                "version": row["version"],
                "timestamp": row.get("created_at"),
                "mutation_type": row.get("mutation_type"),
                "change_summary": row.get("change_summary"),
            }
            for row in revisions
        ]
        return jsonify(
            {
                "ok": True,
                "revisions": revisions,
                "history": history,
                "count": len(revisions),
            }
        )

    @app.post("/api/projects/<project_id>/restore")
    @project_ownership_required
    def restore_project_version(project_id: str):
        data = request.get_json(silent=True) or {}
        try:
            payload = RestoreJDFPayload.model_validate(data)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if payload.version < 1:
            return jsonify({"ok": False, "error": "version must be a positive integer"}), 400
        doc = fetch_jdf_at_version(project_id, payload.version)
        if doc is None:
            return jsonify({"ok": False, "error": f"version {payload.version} not found"}), 404
        workspace_id = (payload.workspace_id or project_id).strip() or project_id
        upsert_draft(workspace_id=workspace_id, content=doc)
        return jsonify({"ok": True, "document": doc, "version": payload.version})

    @app.get("/api/projects/<project_id>/jdf")
    @project_ownership_required
    def get_project_jdf(project_id: str):
        version_raw = request.args.get("version")
        include_omp = request.args.get("include_omp", "false").lower() in ("1", "true", "yes")
        if version_raw is not None:
            try:
                version = int(version_raw)
            except ValueError:
                return jsonify({"error": "version must be an integer"}), 400
            doc = fetch_jdf_at_version(project_id, version)
            if doc is None:
                return jsonify({"error": f"version {version} not found"}), 404
            if include_omp:
                omp_artifact_ids = get_omp_linkages_for_revision(f"rev-{project_id}-{version}")
                doc["ompArtifactIds"] = omp_artifact_ids
            return jsonify(
                {"ok": True, "document": _serve_citation_rows(doc), "version": version}
            )

        doc = fetch_latest_jdf_or_empty(project_id)
        if not doc.get("body"):
            doc.setdefault("meta", {})["title"] = doc.get("meta", {}).get("title") or project_id
        if include_omp:
            doc["ompArtifactIds"] = doc.get("ompArtifactIds") or doc.get("meta", {}).get("ompArtifactIds") or []
        return jsonify({"ok": True, "document": _serve_citation_rows(doc)})

    @app.get("/api/projects/<project_id>/omp")
    @project_ownership_required
    def get_project_omp(project_id: str):
        from ..services.omp import list_omp_artifacts
        artifact_type = request.args.get("type")
        omp_artifacts = list_omp_artifacts(project_id, artifact_type=artifact_type)
        return jsonify({
            "ok": True,
            "artifacts": [a.to_dict() for a in omp_artifacts],
            "count": len(omp_artifacts),
        })

    @app.put("/api/projects/<project_id>/jdf")
    @project_ownership_required
    def put_project_jdf(project_id: str):
        request_id = str(uuid.uuid4())
        start_time = time.perf_counter()
        audit = get_audit_logger()
        data = request.get_json(silent=True) or {}
        try:
            payload = SaveJDFPayload.model_validate(data)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            audit.log_exception(
                request_id,
                project_id,
                "JDF_PUT",
                exc,
                duration_ms=duration_ms,
            )
            return jsonify({"error": str(exc)}), 400

        ver = current_document_version(project_id)
        if ver > 0 and is_version_locked(project_id, ver):
            return jsonify({"ok": False, "error": "Document is locked"}), 409

        try:
            node_id = payload.id or payload.target_node_id
            expected = payload.expected_version
            if payload.node_data and node_id:
                result = patch_jdf_node(
                    project_id,
                    node_id,
                    sanitize_jdf_node(payload.node_data),
                    mutation_type=payload.mutation_type,
                    insert_after_id=payload.insert_after_id,
                    change_summary=payload.change_summary,
                    expected_version=expected,
                )
            else:
                tree = sanitize_jdf_node(_resolve_tree(payload, project_id))
                parse_document(tree)
                result = save_jdf_revision(
                    project_id,
                    tree,
                    mutation_type=payload.mutation_type,
                    target_node_id=payload.target_node_id,
                    change_summary=payload.change_summary,
                    expected_version=expected,
                )

                # Save OMP linkages if present in JDF meta
                meta = tree.get("meta", {})
                omp_artifact_ids = meta.get("ompArtifactIds") or []
                source_artifact_ids = meta.get("sourceArtifactIds") or []
                all_omp_ids = list(set(omp_artifact_ids + source_artifact_ids))
                if all_omp_ids:
                    try:
                        save_omp_linkage(project_id, result.get("revision_id", ""), all_omp_ids)
                    except Exception:
                        pass

            duration_ms = int((time.perf_counter() - start_time) * 1000)
            audit.log_audit(
                request_id,
                project_id,
                "JDF_PUT",
                target_node_id=payload.target_node_id or payload.id,
                success=True,
                duration_ms=duration_ms,
                details={"mutation_type": payload.mutation_type},
            )
            if node_id:
                result["updated_node_id"] = node_id
            return jsonify(result)
        except RevisionConflict as exc:
            return _conflict_payload(exc)
        except ValueError as exc:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            audit.log_exception(
                request_id,
                project_id,
                "JDF_PUT",
                exc,
                target_node_id=payload.target_node_id or payload.id,
                duration_ms=duration_ms,
            )
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            audit.log_exception(
                request_id,
                project_id,
                "JDF_PUT",
                exc,
                target_node_id=payload.target_node_id,
                duration_ms=duration_ms,
            )
            raise

    @app.post("/api/projects/<project_id>/import-jdf")
    @project_ownership_required
    def import_project_jdf(project_id: str):
        """Load a exported ``.jdf`` (or a bare JDF tree) into a project.

        This is the other half of the export: the file a reader took away has to
        come back as the document that was exported, with its verification state
        and its anchors, or "nothing locks you in" is a slogan. The response
        carries ``round_trip`` — the document hash against the one the sidecar
        declared, every anchor resolved against the manifest the file brought with
        it, and the per-state node counts — so a document that lost an anchor
        fails a number here instead of failing silently in a reader's hands.
        """
        request_id = str(uuid.uuid4())
        start_time = time.perf_counter()
        audit = get_audit_logger()
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "Provide the .jdf as a JSON body."}), 400
        source = sidecar_document(payload)
        if not isinstance(source.get("body"), list) or not source.get("body"):
            return jsonify({"ok": False, "error": "JDF body is missing or empty."}), 400
        try:
            tree = sanitize_jdf_node(
                parse_document(
                    {
                        "document_id": str(source.get("document_id") or f"doc-{project_id}"),
                        "meta": dict(source.get("meta") or {}),
                        "truth_ledger": dict(source.get("truth_ledger") or {}),
                        "body": source.get("body") or [],
                    }
                ).model_dump(mode="json")
            )
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            audit.log_exception(
                request_id, project_id, "JDF_IMPORT", exc, duration_ms=duration_ms
            )
            return jsonify({"ok": False, "error": str(exc)}), 400

        report = audit_jdf_payload(
            {
                "format": payload.get("format"),
                "document_sha256": payload.get("document_sha256"),
                "source_manifest": payload.get("source_manifest"),
                "document": tree,
            }
        )
        try:
            result = save_jdf_revision(
                project_id,
                tree,
                mutation_type="JDF_IMPORT",
                change_summary=(
                    f"Imported JDF ({report['anchors_resolved']}/{report['anchors_total']} "
                    "anchors resolved)"
                ),
            )
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            audit.log_exception(
                request_id, project_id, "JDF_IMPORT", exc, duration_ms=duration_ms
            )
            return jsonify({"ok": False, "error": str(exc)}), 500
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        audit.log_audit(
            request_id,
            project_id,
            "JDF_IMPORT",
            success=True,
            duration_ms=duration_ms,
            details={
                "anchors_total": report["anchors_total"],
                "anchors_resolved": report["anchors_resolved"],
                "document_sha256_matches": report["document_sha256_matches"],
            },
        )
        return jsonify({**result, "ok": True, "round_trip": report})

    @app.post("/api/projects/<project_id>/uploads/presign")
    @project_ownership_required
    def presign_project_upload(project_id: str):
        """A browser-direct upload target for a document.

        With S3 configured the client PUTs the bytes straight to the bucket
        (no web replica ever holds them) and then calls ``import-pdf`` with the
        returned ``object_key``. Without S3 the answer is ``mode: "multipart"``
        and the client posts the file to ``import-pdf`` as before.
        """
        data = request.get_json(silent=True) or {}
        filename = str(data.get("filename") or "").strip()
        content_type = str(data.get("content_type") or guess_upload_content_type(filename)).strip()
        try:
            size = int(data.get("size_bytes") or 0)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "size_bytes must be an integer."}), 400
        if not filename:
            return jsonify({"ok": False, "error": "filename required"}), 400
        if not upload_extension_allowed(filename) or not upload_content_type_allowed(content_type):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Only PDF, image (png/jpg/jpeg/tif/tiff/bmp) and text (txt/md) uploads can be presigned.",
                    }
                ),
                400,
            )
        if size and size > max_upload_bytes():
            return jsonify({"ok": False, "error": "File exceeds the upload limit."}), 413
        store = get_object_store()
        key = upload_key(project_id, filename)
        target = store.presign_put(key, content_type=content_type)
        if target is None:
            return jsonify({"ok": True, "mode": "multipart", "upload_url": f"/api/projects/{project_id}/import-pdf"})
        return jsonify(
            {
                "ok": True,
                "mode": "s3",
                "object_key": key,
                "upload": target,
                "complete": {
                    "url": f"/api/projects/{project_id}/import-pdf",
                    "body": {"object_key": key, "filename": filename},
                },
            }
        )

    @app.post("/api/projects/<project_id>/import-pdf")
    @project_ownership_required
    def import_project_pdf(project_id: str):
        """Import a document (PDF, image or text file) as the project's next revision.

        The route name predates the multimodal intake (2026-09-25); images are
        routed by ``services/parser_router.route_intake`` to the scan backend.

        Two input shapes: a multipart ``file`` (the bytes travel through this
        replica once, only to be staged), or a JSON ``{object_key, filename}``
        naming an object the client already uploaded via ``uploads/presign``.

        Two execution modes: with ``PARSE_ASYNC`` (the default when a broker is
        configured) the document is staged in the object store, a worker task
        is queued and the answer is 202 with a ``task_id`` to poll at
        ``GET /api/tasks/<task_id>``; otherwise the same pipeline
        (``services/pdf_ingest``) runs inline and answers 200.
        """
        request_id = str(uuid.uuid4())
        audit = get_audit_logger()
        store = get_object_store()
        object_key: str | None = None
        file_bytes: bytes | None = None

        payload = request.get_json(silent=True) if request.is_json else None
        if isinstance(payload, dict) and payload.get("object_key"):
            object_key = str(payload["object_key"]).strip()
            filename = str(payload.get("filename") or object_key.rsplit("/", 1)[-1]).strip()
            if not key_belongs_to_project(object_key, project_id):
                return jsonify({"ok": False, "error": "object_key does not belong to this project."}), 403
            if not store.exists(object_key):
                return jsonify({"ok": False, "error": "Uploaded object not found."}), 404
            if not parse_async_enabled():
                file_bytes = store.get_bytes(object_key)
        else:
            upload = request.files.get("file")
            if upload is None or not upload.filename:
                return jsonify({"ok": False, "error": "No file uploaded."}), 400
            filename = upload.filename.strip()
            file_bytes = upload.read()
            if not file_bytes:
                return jsonify({"ok": False, "error": "Empty file."}), 400

        if file_bytes is not None:
            try:
                validate_upload_bytes(filename, file_bytes)
            except UploadRejectedError as exc:
                return jsonify({"ok": False, "error": str(exc)}), exc.http_status

        ensure_project(project_id)
        if parse_async_enabled():
            if object_key is None:
                object_key = upload_key(project_id, filename)
                store.put_bytes(object_key, file_bytes or b"", content_type=guess_upload_content_type(filename))
            # The job row exists before the message does, so a poll that beats
            # the worker still finds "queued" rather than nothing.
            job_id = create_job(
                project_id,
                kind="import_pdf",
                filename=filename,
                object_key=object_key,
                size_bytes=len(file_bytes) if file_bytes is not None else None,
            )
            try:
                from ..tasks.parse_tasks import import_project_pdf_task
            except ImportError:
                from tasks.parse_tasks import import_project_pdf_task
            task = import_project_pdf_task.apply_async(args=[project_id, object_key, filename, job_id])
            set_task(job_id, task.id)
            audit.log_audit(
                request_id,
                project_id,
                "PDF_IMPORT",
                success=True,
                details={
                    "filename": filename,
                    "async": True,
                    "task_id": task.id,
                    "job_id": job_id,
                    "object_key": object_key,
                },
            )
            return (
                jsonify(
                    {
                        "ok": True,
                        "status": "queued",
                        "task_id": task.id,
                        "job_id": job_id,
                        "status_url": f"/api/tasks/{task.id}",
                        "job_url": f"/api/projects/{project_id}/ingest-jobs/{job_id}",
                        "filename": filename,
                    }
                ),
                202,
            )

        job_id = create_job(
            project_id,
            kind="import_pdf",
            filename=filename,
            object_key=object_key,
            size_bytes=len(file_bytes or b""),
        )
        try:
            result = ingest_pdf_for_project(project_id, filename, file_bytes or b"", job_id=job_id)
        except PdfIngestError as exc:
            return jsonify({"ok": False, "error": str(exc), "job_id": job_id}), exc.http_status
        if object_key is not None:
            store.delete(object_key)
        return jsonify(result)

    @app.get("/api/projects/<project_id>/nodes/<node_id>/history")
    @project_ownership_required
    def get_node_history(project_id: str, node_id: str):
        revisions = list_node_revisions(project_id, node_id)
        return jsonify(
            {"ok": True, "node_id": node_id, "revisions": revisions, "count": len(revisions)}
        )

    @app.post("/api/projects/<project_id>/nodes/<node_id>/restore")
    @project_ownership_required
    def restore_node_revision(project_id: str, node_id: str):
        request_id = str(uuid.uuid4())
        start_time = time.perf_counter()
        audit = get_audit_logger()
        data = request.get_json(silent=True) or {}
        revision_id = str(data.get("revision_id") or "").strip()
        if not revision_id:
            return jsonify({"ok": False, "error": "revision_id required"}), 400
        node_json = fetch_node_revision(project_id, node_id, revision_id)
        if not node_json:
            return jsonify({"ok": False, "error": "revision not found"}), 404
        tree = fetch_latest_jdf_or_empty(project_id)
        mutated, found = splice_node(tree, node_id, node_json)
        if not found:
            return jsonify({"ok": False, "error": "node not found in document"}), 404
        parse_document(mutated)
        result = save_jdf_revision(
            project_id,
            mutated,
            mutation_type="NODE_RESTORE",
            target_node_id=node_id,
            change_summary=data.get("change_summary") or f"Restored node from {revision_id}",
        )
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        audit.log_audit(
            request_id,
            project_id,
            "NODE_RESTORE",
            target_node_id=node_id,
            success=True,
            duration_ms=duration_ms,
            details={"revision_id": revision_id},
        )
        return jsonify(result)
