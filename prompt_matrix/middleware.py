"""Request guards: project ownership and CSRF helpers."""

from __future__ import annotations

import os
import re
from functools import wraps
from typing import Any, Callable

from flask import jsonify, request, session

_PROJECT_PATH = re.compile(r"^/api/projects/([^/]+)")
_CSRF_EXEMPT_PREFIXES = (
    "/health",
    "/api/health",
    "/api/waitlist",
    "/api/webhook/stripe",
    "/api/webhooks/stripe",
    "/api/sandbox/verify",
    "/api/auth/",
    "/api/substrate",
    "/api/feedback",
    "/api/tester-feedback",
)


def ownership_enforced() -> bool:
    raw = (os.environ.get("ASSURE_ENFORCE_OWNERSHIP") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    try:
        from .cloud_auth import auth_required, current_user_id
    except ImportError:
        from cloud_auth import auth_required, current_user_id
    return bool(auth_required() and current_user_id())


def csrf_enabled() -> bool:
    raw = (os.environ.get("WTF_CSRF_ENABLED") or "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    try:
        from .cloud_auth import is_production_env
    except ImportError:
        from cloud_auth import is_production_env
    return is_production_env()


def check_project_ownership(project_id: str):
    """Return a Flask response when access is denied, else None."""
    if not project_id or not ownership_enforced():
        return None
    try:
        from .cloud_auth import current_user_id
    except ImportError:
        from cloud_auth import current_user_id
    user_id = current_user_id() or session.get("clerk_user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "Authentication required."}), 401
    try:
        from .db.jdf_repository import project_owner_id
    except ImportError:
        from db.jdf_repository import project_owner_id
    owner = project_owner_id(project_id)
    if owner is None:
        return None
    if str(owner) != str(user_id):
        return jsonify({"ok": False, "error": "Forbidden."}), 403
    return None


def project_ownership_required(view: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator: 403 when the session user does not own URL project_id."""

    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        project_id = kwargs.get("project_id")
        if not project_id:
            match = _PROJECT_PATH.match(request.path or "")
            project_id = match.group(1) if match else ""
        denied = check_project_ownership(str(project_id or ""))
        if denied is not None:
            return denied
        return view(*args, **kwargs)

    return wrapped


def register_security_guards(app) -> None:
    """Attach ownership checks to every /api/projects/<id>/* request."""

    @app.before_request
    def _project_ownership_guard():
        path = request.path or ""
        match = _PROJECT_PATH.match(path)
        if not match:
            return None
        if path.rstrip("/") == "/api/projects":
            return None
        return check_project_ownership(match.group(1))


def csrf_exempt_path(path: str) -> bool:
    p = path or ""
    return any(p == prefix or p.startswith(prefix) for prefix in _CSRF_EXEMPT_PREFIXES)
