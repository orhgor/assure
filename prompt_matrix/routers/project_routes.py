"""Project CRUD REST endpoints."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from flask import jsonify, request
from pydantic import BaseModel, ConfigDict

try:
    from ..db.connection import init_db
    from ..db.jdf_repository import ensure_project, save_jdf_revision
    from ..db.project_files import fetch_project_files, save_last_compiled, save_project_source
    from ..db.project_templates_repository import fetch_template
    from ..db.settings_repository import fetch_project_settings, save_project_settings
    from ..history import get_db
    from ..middleware import project_ownership_required
    from ..models.jdf import flatten_nodes
except ImportError:
    from db.connection import init_db
    from db.jdf_repository import ensure_project, save_jdf_revision
    from db.project_files import fetch_project_files, save_last_compiled, save_project_source
    from db.project_templates_repository import fetch_template
    from db.settings_repository import fetch_project_settings, save_project_settings
    from history import get_db
    from middleware import project_ownership_required
    from models.jdf import flatten_nodes


class ProjectSettingsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    show_citations: bool = True


class ProjectCreatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    template_id: str | None = None
    prompt: str | None = None
    source_md: str | None = None
    prompt_id: str | None = None


def _slug(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "project"
    return f"{base}-{uuid.uuid4().hex[:6]}"


def _count_annotations(jdf_tree_raw: str | None) -> dict[str, int]:
    if not jdf_tree_raw:
        return {"node_count": 0, "redhat_count": 0, "z3_violations": 0}
    try:
        tree = json.loads(jdf_tree_raw)
    except (TypeError, json.JSONDecodeError):
        return {"node_count": 0, "redhat_count": 0, "z3_violations": 0}
    nodes = flatten_nodes(tree)
    redhat_count = 0
    z3_violations = 0
    for node in nodes:
        ann = node.get("annotations") or {}
        rh = ann.get("redhat") or []
        if isinstance(rh, list):
            redhat_count += len(rh)
        z3 = ann.get("z3") or []
        if isinstance(z3, list):
            z3_violations += sum(
                1 for z in z3 if isinstance(z, dict) and z.get("status") == "violation"
            )
    return {
        "node_count": len(nodes),
        "redhat_count": redhat_count,
        "z3_violations": z3_violations,
    }


def _project_status(
    *,
    node_count: int,
    lock_count: int,
    redhat_count: int,
    z3_violations: int,
    current_version: int,
) -> str:
    if node_count == 0:
        return "drafting"
    if z3_violations > 0:
        return "verifying"
    if redhat_count > 0:
        return "audited"
    if node_count > 0 and z3_violations == 0 and current_version >= 1:
        return "ready_to_export"
    return "drafting"


def _dashboard_payload(
    row: tuple[Any, ...],
) -> dict[str, Any]:
    tl = row[5]
    jdf_tree = row[6] if len(row) > 6 else None
    try:
        lock_count = len(json.loads(tl)) if tl else 0
    except (TypeError, json.JSONDecodeError):
        lock_count = 0
    counts = _count_annotations(jdf_tree)
    current_version = int(row[2] or 1)
    status = _project_status(
        node_count=counts["node_count"],
        lock_count=lock_count,
        redhat_count=counts["redhat_count"],
        z3_violations=counts["z3_violations"],
        current_version=current_version,
    )
    return {
        "id": row[0],
        "title": row[1],
        "current_version": current_version,
        "created_at": row[3],
        "updated_at": row[4],
        "lock_count": lock_count,
        "node_count": counts["node_count"],
        "redhat_count": counts["redhat_count"],
        "z3_violations": counts["z3_violations"],
        "status": status,
        "source_count": int(row[7] or 0) if len(row) > 7 else 0,
    }


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
                    ORDER BY r.version DESC LIMIT 1) as truth_ledger,
                   (SELECT r.jdf_tree FROM jdf_revisions r
                    WHERE r.project_id = p.id
                    ORDER BY r.version DESC LIMIT 1) as jdf_tree,
                   (SELECT COUNT(*) FROM substrate_vault sv
                    WHERE sv.project_id = p.id) as source_count
            FROM projects p
            ORDER BY p.updated_at DESC, p.title ASC
            """
        ).fetchall()
        projects = [_dashboard_payload(row) for row in rows]
        return jsonify({"ok": True, "projects": projects, "count": len(projects)})

    @app.post("/api/projects")
    def create_project():
        data = request.get_json(silent=True) or {}
        try:
            payload = ProjectCreatePayload.model_validate(data)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        title = payload.title.strip()
        if not title:
            return jsonify({"error": "title required"}), 400
        project_id = _slug(title)
        owner = None
        try:
            from ..cloud_auth import current_user_id
        except ImportError:
            try:
                from cloud_auth import current_user_id
            except ImportError:
                current_user_id = lambda: None  # noqa: E731
        owner = current_user_id()
        ensure_project(project_id, title, owner_id=owner)

        template = fetch_template(payload.template_id) if payload.template_id else None
        default_prompt = ""
        if template:
            structure = template.get("jdf_structure") or {}
            body = structure.get("body") if isinstance(structure, dict) else []
            document = {
                "document_id": f"doc-{project_id}",
                "meta": {"project_id": project_id, "template_id": template["id"]},
                "truth_ledger": {},
                "body": body if isinstance(body, list) else [],
            }
            save_jdf_revision(
                project_id,
                document,
                mutation_type="WIZARD_INIT",
                change_summary=f"Created from template {template['name']}",
            )
            default_prompt = str(template.get("default_prompt") or "")

        prompt_text = (payload.prompt or default_prompt or "").strip()
        if payload.prompt_id and not prompt_text:
            try:
                from ..db.prompts_repository import fetch_prompt
            except ImportError:
                from db.prompts_repository import fetch_prompt
            row = fetch_prompt(payload.prompt_id)
            if row:
                prompt_text = str(row.get("content") or "").strip()

        source_md = (payload.source_md or prompt_text or "").strip()
        if source_md:
            save_project_source(project_id, source_md)

        return jsonify(
            {
                "ok": True,
                "id": project_id,
                "title": title,
                "template_id": template["id"] if template else None,
                "prompt": prompt_text,
            }
        ), 201

    @app.patch("/api/projects/<project_id>")
    @project_ownership_required
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
    @project_ownership_required
    def delete_project(project_id: str):
        if project_id == "default":
            return jsonify({"error": "Cannot delete the default project"}), 403
        db = get_db()
        db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        db.commit()
        return jsonify({"ok": True})

    @app.get("/api/projects/<project_id>/files")
    @project_ownership_required
    def get_project_files(project_id: str):
        return jsonify(fetch_project_files(project_id))

    @app.put("/api/projects/<project_id>/files")
    @project_ownership_required
    def put_project_files(project_id: str):
        data = request.get_json(silent=True) or {}
        result = None
        if "source_md" in data:
            result = save_project_source(project_id, str(data.get("source_md") or ""))
        if data.get("document") is not None:
            result = save_last_compiled(project_id, data["document"])
        elif data.get("manifest") is not None:
            result = save_last_compiled(project_id, data["manifest"])
        elif data.get("lastCompiledOutput") is not None:
            result = save_last_compiled(project_id, data["lastCompiledOutput"])
        if result is None:
            result = fetch_project_files(project_id)
        return jsonify(result)

    @app.get("/api/projects/<project_id>/settings")
    @project_ownership_required
    def get_project_settings(project_id: str):
        settings = fetch_project_settings(project_id)
        return jsonify({"ok": True, "settings": settings})

    @app.put("/api/projects/<project_id>/settings")
    @project_ownership_required
    def put_project_settings(project_id: str):
        data = request.get_json(silent=True) or {}
        try:
            payload = ProjectSettingsPayload.model_validate(data)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        settings = save_project_settings(project_id, show_citations=payload.show_citations)
        return jsonify({"ok": True, "settings": settings})
