"""Import config routes and global audit log API."""

from __future__ import annotations

from flask import jsonify, request

try:
    from ..cloud_auth import current_user_id
    from ..db.jdf_repository import fetch_latest_jdf_or_empty, save_jdf_revision
    from ..db.user_activity_repository import list_user_activity
    from ..lib.sanitize import sanitize_jdf_node
    from ..middleware import project_ownership_required
    from ..models.jdf import parse_document
    from ..services.config_import import (
        config_to_jdf_section,
        merge_config_into_document,
        parse_config_bytes,
    )
    from ..upload_limits import UploadRejectedError, validate_upload_bytes
except ImportError:
    from cloud_auth import current_user_id
    from db.jdf_repository import fetch_latest_jdf_or_empty, save_jdf_revision
    from db.user_activity_repository import list_user_activity
    from lib.sanitize import sanitize_jdf_node
    from middleware import project_ownership_required
    from models.jdf import parse_document
    from services.config_import import (
        config_to_jdf_section,
        merge_config_into_document,
        parse_config_bytes,
    )
    from upload_limits import UploadRejectedError, validate_upload_bytes


def register_import_config_routes(app) -> None:
    @app.post("/api/projects/<project_id>/import-config")
    @project_ownership_required
    def import_project_config(project_id: str):
        title = (request.form.get("title") or "Imported Config").strip()
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            data = request.get_json(silent=True) or {}
            raw_config = data.get("config")
            if raw_config is None:
                return jsonify({"ok": False, "error": "Provide file or JSON config body."}), 400
            if not isinstance(raw_config, dict):
                return jsonify({"ok": False, "error": "config must be a JSON object."}), 400
            parsed = raw_config
            title = str(data.get("title") or title)
        else:
            filename = upload.filename.strip()
            file_bytes = upload.read()
            if not file_bytes:
                return jsonify({"ok": False, "error": "Empty file."}), 400
            try:
                validate_upload_bytes(filename, file_bytes)
            except UploadRejectedError as exc:
                return jsonify({"ok": False, "error": str(exc)}), exc.http_status
            try:
                parsed = parse_config_bytes(file_bytes, filename)
            except ValueError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400
            except Exception as exc:
                return jsonify({"ok": False, "error": f"Parse error: {exc}"}), 400

        doc = fetch_latest_jdf_or_empty(project_id)
        section = config_to_jdf_section(parsed, title=title)
        tree = sanitize_jdf_node(merge_config_into_document(doc, section))
        parse_document(tree)
        result = save_jdf_revision(
            project_id,
            tree,
            mutation_type="CONFIG_IMPORT",
            change_summary=f"Imported config: {title}",
        )
        return jsonify({"ok": True, "section_id": section["id"], **result})


def register_audit_log_routes(app) -> None:
    @app.get("/api/audit-log")
    def get_audit_log():
        user_id = current_user_id() or request.args.get("user_id")
        if not user_id:
            return jsonify({"ok": False, "error": "Authentication required."}), 401
        project_id = (request.args.get("project_id") or "").strip() or None
        limit_raw = request.args.get("limit") or "100"
        try:
            limit = min(int(limit_raw), 500)
        except ValueError:
            limit = 100
        entries = list_user_activity(user_id=str(user_id), project_id=project_id, limit=limit)
        return jsonify({"ok": True, "entries": entries, "count": len(entries)})
