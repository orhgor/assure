"""POST /api/projects/<id>/nodes/<node_id>/ground — hybrid search/LLM rewrite."""

from __future__ import annotations

from typing import Any

from flask import jsonify, request
from pydantic import BaseModel

try:
    from ..middleware import project_ownership_required
    from ..services.ground_node import ground_node
except ImportError:
    from middleware import project_ownership_required
    from services.ground_node import ground_node


class GroundPayload(BaseModel):
    mode: str = "auto"
    document: dict[str, Any] | None = None


def register_ground_routes(app) -> None:
    @app.post("/api/projects/<project_id>/nodes/<node_id>/ground")
    @project_ownership_required
    def ground_project_node(project_id: str, node_id: str):
        data = request.get_json(silent=True) or {}
        try:
            payload = GroundPayload.model_validate(data)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        try:
            result = ground_node(
                project_id,
                node_id,
                mode=payload.mode,
                document=payload.document,
            )
        except KeyError:
            return jsonify({"ok": False, "error": "node not found"}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 502
        return jsonify(result)
