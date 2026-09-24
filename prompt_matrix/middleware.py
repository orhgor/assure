"""Request guards: project ownership and CSRF helpers."""

from __future__ import annotations

import os
import re
from functools import wraps
from typing import Any, Callable

from flask import g, jsonify, request, session

try:
    from .service_auth import is_service_api_request
except ImportError:
    from service_auth import is_service_api_request

_PROJECT_PATH = re.compile(r"^/api/projects/([^/]+)")
_CSRF_EXEMPT_PREFIXES = (
    "/health",
    "/api/health",
    "/api/waitlist",
    "/api/webhook/stripe",
    "/api/webhooks/stripe",
    "/api/sandbox/verify",
    "/api/auth/",
    "/api/feedback",
    "/api/tester-feedback",
)


def ownership_enforced() -> bool:
    raw = (os.environ.get("ASSURE_ENFORCE_OWNERSHIP") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    if is_service_api_request():
        return False
    try:
        from .cloud_auth import auth_required, current_user_id, is_self_hosted, require_clerk_login
    except ImportError:
        from cloud_auth import auth_required, current_user_id, is_self_hosted, require_clerk_login
    if is_self_hosted() and not require_clerk_login():
        return False
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
    if is_service_api_request():
        return None
    try:
        from .cloud_auth import ROLE_ADMIN, current_role, current_user_id
    except ImportError:
        from cloud_auth import ROLE_ADMIN, current_role, current_user_id
    user_id = current_user_id() or session.get("clerk_user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "Authentication required."}), 401
    # Admin owns the workspace, not just its own rows: every project, including
    # the pre-auth ones backfilled to the `legacy` owner, stays reachable.
    if current_role() == ROLE_ADMIN:
        return None
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


# Projects that exist without a creation request: seeded at boot by
# ``routers/sandbox.ensure_sandbox_project`` (``sandbox``, ``founder``) or created
# on first touch (``default``). A read for one of these before its row exists is
# not a ghost.
AUTO_CREATED_PROJECT_IDS = frozenset({"default", "sandbox", "founder"})


def _auto_created_project_ids() -> frozenset[str]:
    """``AUTO_CREATED_PROJECT_IDS`` plus the sandbox id as ``routers/sandbox.py`` defines it.

    Imported lazily: ``routers.sandbox`` pulls in the draft pipeline, which
    imports this module.
    """
    try:
        from .routers.sandbox import SANDBOX_PROJECT_ID
    except ImportError:
        try:
            from routers.sandbox import SANDBOX_PROJECT_ID
        except ImportError:
            return AUTO_CREATED_PROJECT_IDS
    return AUTO_CREATED_PROJECT_IDS | {SANDBOX_PROJECT_ID}

# Reads. A PUT/POST/PATCH may be the request that creates the project
# (``save_jdf_revision`` → ``ensure_project``), so only these are gated.
_READ_METHODS = frozenset({"GET", "HEAD", "DELETE"})


def check_project_exists(project_id: str):
    """404 for a read of a project with no ``projects`` row; else None.

    ``GET /api/projects/<id>/jdf`` and ``/export`` used to answer 200 with an
    empty document for any id at all — ``fetch_latest_jdf_or_empty`` builds one
    — so a mistyped or deleted project looked like a real, blank one (2026-09-22
    audit). Writes are left alone: the first PUT is how a project comes to exist.
    """
    if not project_id or project_id in _auto_created_project_ids():
        return None
    if request.method not in _READ_METHODS:
        return None
    try:
        from .db.jdf_repository import project_exists
    except ImportError:
        from db.jdf_repository import project_exists
    if project_exists(project_id):
        return None
    return jsonify({"ok": False, "error": "Project not found."}), 404


def project_ownership_required(view: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator: 403 when the session user does not own URL project_id; 404 for a read of a project that does not exist."""

    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        project_id = kwargs.get("project_id")
        if not project_id:
            match = _PROJECT_PATH.match(request.path or "")
            project_id = match.group(1) if match else ""
        # The before_request guard already ran the ownership check for this
        # project on this request; do not pay for it twice (audit 2026-09-24).
        if getattr(g, "_assure_ownership_checked", None) != str(project_id or ""):
            denied = check_project_ownership(str(project_id or ""))
            if denied is not None:
                return denied
        missing = check_project_exists(str(project_id or ""))
        if missing is not None:
            return missing
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
        denied = check_project_ownership(match.group(1))
        if denied is None:
            g._assure_ownership_checked = match.group(1)
        return denied


def csrf_exempt_path(path: str) -> bool:
    p = path or ""
    return is_service_api_request(p) or any(p == prefix or p.startswith(prefix) for prefix in _CSRF_EXEMPT_PREFIXES)
