"""JDF persistence REST endpoints with positional docking."""

from __future__ import annotations

import time
import uuid
from typing import Any

from flask import jsonify, request
from pydantic import BaseModel, ConfigDict

try:
    from ..db.document_lock_repository import is_version_locked
    from ..db.jdf_repository import (
        RevisionConflict,
        current_document_version,
        fetch_jdf_at_version,
        fetch_latest_jdf_or_empty,
        list_jdf_revisions,
        patch_jdf_node,
        save_jdf_revision,
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
    from ..services.pdf_import import pdf_bytes_to_jdf
    from ..upload_limits import UploadRejectedError, validate_upload_bytes
except ImportError:
    from db.document_lock_repository import is_version_locked
    from db.jdf_repository import (
        RevisionConflict,
        current_document_version,
        fetch_jdf_at_version,
        fetch_latest_jdf_or_empty,
        list_jdf_revisions,
        patch_jdf_node,
        save_jdf_revision,
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
    from services.pdf_import import pdf_bytes_to_jdf
    from upload_limits import UploadRejectedError, validate_upload_bytes


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


def register_jdf_routes(app) -> None:
    @app.get("/api/projects/<project_id>/history")
    @project_ownership_required
    def get_project_history(project_id: str):
        revisions = list_jdf_revisions(project_id)
        return jsonify({"ok": True, "revisions": revisions, "count": len(revisions)})

    @app.get("/api/projects/<project_id>/jdf")
    @project_ownership_required
    def get_project_jdf(project_id: str):
        version_raw = request.args.get("version")
        if version_raw is not None:
            try:
                version = int(version_raw)
            except ValueError:
                return jsonify({"error": "version must be an integer"}), 400
            doc = fetch_jdf_at_version(project_id, version)
            if doc is None:
                return jsonify({"error": f"version {version} not found"}), 404
            return jsonify({"ok": True, "document": doc, "version": version})

        doc = fetch_latest_jdf_or_empty(project_id)
        if not doc.get("body"):
            doc.setdefault("meta", {})["title"] = doc.get("meta", {}).get("title") or project_id
        return jsonify({"ok": True, "document": doc})

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

    @app.post("/api/projects/<project_id>/import-pdf")
    @project_ownership_required
    def import_project_pdf(project_id: str):
        request_id = str(uuid.uuid4())
        start_time = time.perf_counter()
        audit = get_audit_logger()
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify({"ok": False, "error": "No file uploaded."}), 400
        file_bytes = upload.read()
        if not file_bytes:
            return jsonify({"ok": False, "error": "Empty file."}), 400
        try:
            validate_upload_bytes(upload.filename.strip(), file_bytes)
        except UploadRejectedError as exc:
            return jsonify({"ok": False, "error": str(exc)}), exc.http_status
        try:
            tree = sanitize_jdf_node(
                pdf_bytes_to_jdf(
                    file_bytes,
                    project_id=project_id,
                    filename=upload.filename.strip(),
                )
            )
            parse_document(tree)
            result = save_jdf_revision(
                project_id,
                tree,
                mutation_type="PDF_IMPORT",
                change_summary=f"Imported {upload.filename}",
            )
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            audit.log_exception(
                request_id,
                project_id,
                "PDF_IMPORT",
                exc,
                duration_ms=duration_ms,
            )
            return jsonify({"ok": False, "error": str(exc)}), 400
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        audit.log_audit(
            request_id,
            project_id,
            "PDF_IMPORT",
            success=True,
            duration_ms=duration_ms,
            details={"filename": upload.filename, "version": result.get("version")},
        )
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
