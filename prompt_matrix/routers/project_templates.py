"""GET /api/project-templates — wizard type step."""

from __future__ import annotations

from flask import jsonify

try:
    from ..db.project_templates_repository import list_templates
except ImportError:
    from db.project_templates_repository import list_templates


def register_project_template_routes(app) -> None:
    @app.get("/api/project-templates")
    def get_project_templates():
        return jsonify({"ok": True, "templates": list_templates()})
