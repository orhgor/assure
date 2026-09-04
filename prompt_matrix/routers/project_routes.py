"""Project CRUD REST endpoints."""

from __future__ import annotations

import json
import re
import uuid

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


def _slug(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "project"
    return f"{base}-{uuid.uuid4().hex[:6]}"


def register_project_routes(app) -> None:
    @app.get("/api/projects")
    def list_projects():
        init_db()
        db = get_db()
        ensure_project("default", "Default project")
        rows = db.execute(
            """
            SELECT p.id, p.title, p.current_version, p.created_at, p.updated_at,
                   (SELECT r.truth_ledger FROM jdf_revisions r
                    WHERE r.project_id = p.id
                    ORDER BY r.version DESC LIMIT 1) as truth_ledger
            FROM projects p
            ORDER BY p.updated_at DESC, p.title ASC
            """
        ).fetchall()
        projects = []
        for row in rows:
            tl = row[5]
            try:
                lock_count = len(json.loads(tl)) if tl else 0
            except Exception:
                lock_count = 0
            projects.append(
                {
                    "id": row[0],
                    "title": row[1],
                    "current_version": int(row[2] or 1),
                    "created_at": row[3],
                    "updated_at": row[4],
                    "lock_count": lock_count,
                }
            )
        return jsonify({"ok": True, "projects": projects, "count": len(projects)})

    @app.post("/api/projects")
    def create_project():
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title required"}), 400
        project_id = _slug(title)
        ensure_project(project_id, title)
        return jsonify({"ok": True, "id": project_id, "title": title}), 201

    @app.patch("/api/projects/<project_id>")
    def rename_project(project_id: str):
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title required"}), 400
        db = get_db()
        db.execute(
            "UPDATE projects SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (title, project_id),
        )
        db.commit()
        return jsonify({"ok": True, "id": project_id, "title": title})

    @app.delete("/api/projects/<project_id>")
    def delete_project(project_id: str):
        if project_id == "default":
            return jsonify({"error": "Cannot delete the default project"}), 403
        db = get_db()
        db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        db.commit()
        return jsonify({"ok": True})

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
