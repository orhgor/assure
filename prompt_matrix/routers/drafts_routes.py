"""Founder workbench draft persistence API."""

from __future__ import annotations

from flask import jsonify, request
from pydantic import BaseModel, ConfigDict

try:
    from ..db.drafts_repository import fetch_draft, upsert_draft
except ImportError:
    from db.drafts_repository import fetch_draft, upsert_draft


class DraftSavePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workspace_id: str = "default"
    content: dict = {}


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
