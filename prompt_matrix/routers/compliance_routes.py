"""Sign-off and document lock routes (v1.5)."""

from __future__ import annotations

import uuid

from typing import Any, Literal

from flask import jsonify, request
from pydantic import BaseModel

try:
    from ..cloud_auth import current_user_id
    from ..db.document_lock_repository import (
        create_document_lock,
        fetch_lock_for_version,
        latest_lock,
    )
    from ..db.jdf_repository import current_document_version, fetch_latest_jdf_or_empty
    from ..db.sign_off_repository import create_sign_off, list_sign_offs
    from ..lib.logger import get_audit_logger
    from ..middleware import project_ownership_required
except ImportError:
    from cloud_auth import current_user_id
    from db.document_lock_repository import (
        create_document_lock,
        fetch_lock_for_version,
        latest_lock,
    )
    from db.jdf_repository import current_document_version, fetch_latest_jdf_or_empty
    from db.sign_off_repository import create_sign_off, list_sign_offs
    from lib.logger import get_audit_logger
    from middleware import project_ownership_required


class SignOffPayload(BaseModel):
    status: Literal["approved", "rejected", "pending"] = "approved"
    reviewer_name: str | None = None
    comment: str | None = None


def _reviewer_id() -> str:
    return str(current_user_id() or "anonymous")


def register_compliance_routes(app) -> None:
    @app.get("/api/projects/<project_id>/sign-offs")
    @project_ownership_required
    def get_sign_offs(project_id: str):
        return jsonify({"ok": True, "sign_offs": list_sign_offs(project_id)})

    @app.post("/api/projects/<project_id>/sign-off")
    @project_ownership_required
    def document_sign_off(project_id: str):
        data = request.get_json(silent=True) or {}
        try:
            payload = SignOffPayload.model_validate(data)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        row = create_sign_off(
            project_id,
            reviewer_id=_reviewer_id(),
            status=payload.status,
            reviewer_name=payload.reviewer_name,
            comment=payload.comment,
        )
        return jsonify({"ok": True, "sign_off": row}), 201

    @app.post("/api/projects/<project_id>/nodes/<node_id>/sign-off")
    @project_ownership_required
    def node_sign_off(project_id: str, node_id: str):
        data = request.get_json(silent=True) or {}
        try:
            payload = SignOffPayload.model_validate(data)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        row = create_sign_off(
            project_id,
            reviewer_id=_reviewer_id(),
            status=payload.status,
            node_id=node_id,
            reviewer_name=payload.reviewer_name,
            comment=payload.comment,
        )
        return jsonify({"ok": True, "sign_off": row}), 201

    @app.post("/api/projects/<project_id>/lock")
    @project_ownership_required
    def lock_document(project_id: str):
        request_id = str(uuid.uuid4())
        audit = get_audit_logger()
        tree = fetch_latest_jdf_or_empty(project_id)
        version = current_document_version(project_id)
        locked_by = _reviewer_id()
        lock = create_document_lock(project_id, tree, locked_by=locked_by, version=version)
        audit.log_audit(
            request_id,
            project_id,
            "DOCUMENT_LOCK",
            success=True,
            details={"version": version, "hash": lock.get("content_hash", "")[:16]},
        )
        return jsonify({"ok": True, "lock": lock})

    @app.get("/api/projects/<project_id>/lock")
    @project_ownership_required
    def get_document_lock(project_id: str):
        version_raw = request.args.get("version")
        if version_raw is not None:
            try:
                ver = int(version_raw)
            except ValueError:
                return jsonify({"ok": False, "error": "version must be integer"}), 400
            lock = fetch_lock_for_version(project_id, ver)
        else:
            lock = latest_lock(project_id)
        return jsonify({"ok": True, "lock": lock, "locked": lock is not None})
