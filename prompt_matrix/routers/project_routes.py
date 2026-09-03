"""Project listing REST endpoints."""

from __future__ import annotations

from flask import jsonify, request
from pydantic import BaseModel

try:
    from ..db.connection import init_db
    from ..db.jdf_repository import ensure_project
    from ..db.settings_repository import fetch_project_settings, save_project_settings
    from ..history import get_db
except ImportError:
    from db.connection import init_db
    from db.jdf_repository import ensure_project
    from db.settings_repository import fetch_project_settings, save_project_settings
    from history import get_db


class ProjectSettingsPayload(BaseModel):
    show_citations: bool = True


def register_project_routes(app) -> None:
    @app.get("/api/projects")
    def list_projects():
        init_db()
        db = get_db()
        ensure_project("default", "Default project")
        rows = db.execute(
            """
            SELECT id, title, current_version, created_at, updated_at
            FROM projects
            ORDER BY updated_at DESC, title ASC
            """
        ).fetchall()
        projects = [
            {
                "id": row[0],
                "title": row[1],
                "current_version": int(row[2] or 1),
                "created_at": row[3],
                "updated_at": row[4],
            }
            for row in rows
        ]
        return jsonify({"ok": True, "projects": projects, "count": len(projects)})

    @app.get("/api/projects/<project_id>/settings")
    def get_project_settings(project_id: str):
        settings = fetch_project_settings(project_id)
        return jsonify({"ok": True, "settings": settings})

    @app.put("/api/projects/<project_id>/settings")
    def put_project_settings(project_id: str):
        data = request.get_json(silent=True) or {}
        try:
            payload = ProjectSettingsPayload.model_validate(data)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        settings = save_project_settings(project_id, show_citations=payload.show_citations)
        return jsonify({"ok": True, "settings": settings})
