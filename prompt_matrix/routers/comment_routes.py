"""REST routes for node-scoped project comments."""

from __future__ import annotations

from flask import jsonify, request
from pydantic import BaseModel, Field

try:
    from ..db.comment_repository import add_comment, delete_comment, list_comments
except ImportError:
    from db.comment_repository import add_comment, delete_comment, list_comments


class CommentPayload(BaseModel):
    node_id: str = Field(min_length=1)
    body: str = Field(min_length=1)
    author: str = ""


def register_comment_routes(app) -> None:
    @app.get("/api/projects/<project_id>/comments")
    def get_comments(project_id: str):
        node_id = (request.args.get("node_id") or "").strip() or None
        comments = list_comments(project_id, node_id=node_id)
        return jsonify({"ok": True, "comments": comments, "count": len(comments)})

    @app.post("/api/projects/<project_id>/comments")
    def post_comment(project_id: str):
        data = request.get_json(silent=True) or {}
        try:
            payload = CommentPayload.model_validate(data)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        try:
            row = add_comment(
                project_id,
                payload.node_id.strip(),
                payload.body,
                author=payload.author,
            )
            return jsonify({"ok": True, "comment": row}), 201
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.delete("/api/projects/<project_id>/comments/<comment_id>")
    def remove_comment(project_id: str, comment_id: str):
        if delete_comment(project_id, comment_id):
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "Comment not found."}), 404
