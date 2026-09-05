"""JDF persistence REST endpoints with positional docking."""

from __future__ import annotations

import time
import uuid
from typing import Any

from flask import jsonify, request
from pydantic import BaseModel

try:
    from ..db.jdf_repository import (
        fetch_jdf_at_version,
        fetch_latest_jdf_or_empty,
        list_jdf_revisions,
        patch_jdf_node,
        save_jdf_revision,
    )
    from ..lib.logger import get_audit_logger
    from ..models.jdf import (
        JDFDocumentTree,
        document_to_dict,
        insert_node_after_anchor,
        parse_document,
        splice_node,
    )
except ImportError:
    from db.jdf_repository import (
        fetch_jdf_at_version,
        fetch_latest_jdf_or_empty,
        list_jdf_revisions,
        patch_jdf_node,
        save_jdf_revision,
    )
    from lib.logger import get_audit_logger
    from models.jdf import (
        JDFDocumentTree,
        document_to_dict,
        insert_node_after_anchor,
        parse_document,
        splice_node,
    )


class SaveJDFPayload(BaseModel):
    document: JDFDocumentTree | None = None
    mutation_type: str = "NODE_DOCK"
    target_node_id: str | None = None
    insert_after_id: str | None = None
    new_node: dict[str, Any] | None = None
    change_summary: str | None = None
    id: str | None = None
    node_data: dict[str, Any] | None = None


def _resolve_tree(payload: SaveJDFPayload, project_id: str) -> dict[str, Any]:
    if payload.document is not None:
        tree = document_to_dict(payload.document)
    else:
        tree = fetch_latest_jdf_or_empty(project_id)

    if payload.new_node and payload.insert_after_id:
        tree, _ = insert_node_after_anchor(tree, payload.insert_after_id, payload.new_node)
    elif payload.new_node and payload.target_node_id:
        tree, _ = splice_node(tree, payload.target_node_id, payload.new_node)
    elif payload.document is None and not payload.new_node:
        raise ValueError("document or new_node required")

    return tree


def register_jdf_routes(app) -> None:
    @app.get("/api/projects/<project_id>/history")
    def get_project_history(project_id: str):
        revisions = list_jdf_revisions(project_id)
        return jsonify({"ok": True, "revisions": revisions, "count": len(revisions)})

    @app.get("/api/projects/<project_id>/jdf")
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
        try:
            node_id = payload.id or payload.target_node_id
            if payload.node_data and node_id:
                result = patch_jdf_node(
                    project_id,
                    node_id,
                    payload.node_data,
                    mutation_type=payload.mutation_type,
                    insert_after_id=payload.insert_after_id,
                    change_summary=payload.change_summary,
                )
            else:
                tree = _resolve_tree(payload, project_id)
                # Strict validation before SQLite write
                parse_document(tree)
                result = save_jdf_revision(
                    project_id,
                    tree,
                    mutation_type=payload.mutation_type,
                    target_node_id=payload.target_node_id,
                    change_summary=payload.change_summary,
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
