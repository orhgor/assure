"""Request middleware for global user activity audit log."""

from __future__ import annotations

import json
import re
from typing import Any

from flask import Flask, g, request

_PROJECT_PATH = re.compile(r"^/api/projects/([^/]+)")
_SENSITIVE_KEYS = frozenset({"password", "token", "secret", "api_key", "authorization"})


def _extract_project_id(path: str) -> str | None:
    match = _PROJECT_PATH.match(path or "")
    return match.group(1) if match else None


def _sanitize_payload(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    for key, val in data.items():
        if str(key).lower() in _SENSITIVE_KEYS:
            out[key] = "[redacted]"
        elif isinstance(val, dict):
            out[key] = _sanitize_payload(val)
        else:
            out[key] = val
    return out


def _action_label(method: str, path: str) -> str:
    if "/export" in path:
        return "EXPORT"
    return method.upper()


def register_activity_audit_middleware(app: Flask) -> None:
    @app.before_request
    def _capture_activity_context() -> None:
        g.activity_start_path = request.path
        g.activity_project_id = _extract_project_id(request.path)

    @app.after_request
    def _log_user_activity(response):  # type: ignore[no-untyped-def]
        path = getattr(g, "activity_start_path", request.path)
        if not path.startswith("/api/"):
            return response
        if path.startswith("/api/health"):
            return response
        try:
            from prompt_matrix.cloud_auth import current_user_id
            from prompt_matrix.db.user_activity_repository import log_user_activity
        except ImportError:
            try:
                from ..cloud_auth import current_user_id
                from ..db.user_activity_repository import log_user_activity
            except ImportError:
                return response

        user_id = current_user_id() or "anonymous"
        method = request.method.upper()
        action = _action_label(method, path)
        details: dict[str, Any] = {
            "method": method,
            "path": path,
            "status_code": response.status_code,
        }
        if request.is_json:
            body = request.get_json(silent=True)
            if isinstance(body, dict):
                details["payload"] = _sanitize_payload(body)
        project_id = getattr(g, "activity_project_id", None) or _extract_project_id(path)
        try:
            log_user_activity(user_id, action, project_id=project_id, details=details)
        except Exception:
            pass
        return response
