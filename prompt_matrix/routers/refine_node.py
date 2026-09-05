"""POST /api/projects/<id>/refine-node — surgical rewrite of one JDF node."""

from __future__ import annotations

from typing import Any

from flask import jsonify, request
from pydantic import BaseModel, Field

try:
    from ..services.refine_node import run_refine_node
except ImportError:
    from services.refine_node import run_refine_node


class RefineNodePayload(BaseModel):
    node_id: str
    user_instruction: str = ""
    context: dict[str, Any] | None = None
    document: dict[str, Any] | None = None
    ground_from_vault: bool = False
    substrate_file_ids: list[str] = Field(default_factory=list)
    project_id: str | None = None


def _handle(project_id: str, data: dict[str, Any]):
    try:
        payload = RefineNodePayload.model_validate(data)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    node_id = (payload.node_id or "").strip()
    if not node_id:
        return jsonify({"ok": False, "error": "node_id is required"}), 400
    try:
        result = run_refine_node(
            project_id,
            node_id=node_id,
            user_instruction=payload.user_instruction,
            document=payload.document,
            context=payload.context,
            ground_from_vault=payload.ground_from_vault,
            substrate_file_ids=payload.substrate_file_ids,
        )
    except KeyError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify(result)


def register_refine_node_routes(app) -> None:
    @app.post("/api/projects/<project_id>/refine-node")
    def refine_node(project_id: str):
        return _handle(project_id, request.get_json(silent=True) or {})

    @app.post("/refine-node")
    def refine_node_alias():
        data = request.get_json(silent=True) or {}
        project_id = str(data.get("project_id") or "default")
        return _handle(project_id, data)
