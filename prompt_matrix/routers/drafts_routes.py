"""Founder workbench draft persistence API."""

from __future__ import annotations

from typing import Any

from flask import jsonify, request
from pydantic import BaseModel, ConfigDict

try:
    from ..db.drafts_repository import fetch_draft, upsert_draft
    from ..db.jdf_repository import save_jdf_revision
    from ..lib.sanitize import sanitize_jdf_node
    from ..middleware import project_ownership_required
    from ..models.jdf import parse_document
except ImportError:
    from db.drafts_repository import fetch_draft, upsert_draft
    from db.jdf_repository import save_jdf_revision
    from lib.sanitize import sanitize_jdf_node
    from middleware import project_ownership_required
    from models.jdf import parse_document


class DraftSavePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workspace_id: str = "default"
    content: dict = {}


class ProjectDraftSavePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: dict[str, Any] = {}


def register_drafts_routes(app) -> None:
    @app.put("/api/drafts")
    def save_draft():
        try:
            payload = DraftSavePayload.model_validate(request.get_json(silent=True) or {})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if not payload.workspace_id.strip():
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        draft = upsert_draft(workspace_id=payload.workspace_id, content=payload.content)
        return jsonify({"ok": True, "draft": draft})

    @app.get("/api/drafts")
    def get_draft():
        workspace_id = request.args.get("workspace_id") or "default"
        draft = fetch_draft(workspace_id)
        if not draft:
            return jsonify({"ok": True, "draft": None})
        return jsonify({"ok": True, "draft": draft})

    @app.post("/api/projects/<project_id>/draft")
    @project_ownership_required
    def save_project_draft(project_id: str):
        """Persist founder editor state to drafts + jdf_revisions before export."""
        try:
            payload = ProjectDraftSavePayload.model_validate(request.get_json(silent=True) or {})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        raw = dict(payload.content or {})
        if not raw:
            return jsonify({"ok": False, "error": "content is required"}), 400
        try:
            tree = sanitize_jdf_node(raw)
            parse_document(tree)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        workspace_id = project_id.strip() or "founder"
        upsert_draft(workspace_id=workspace_id, content=tree)
        save_jdf_revision(
            project_id,
            tree,
            mutation_type="PRE_EXPORT_SAVE",
            change_summary="Saved draft before export",
        )
        return jsonify({"ok": True, "document": tree})
