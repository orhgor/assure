"""Roles, permissions and the local sign-in guard.

Five roles, one fixed matrix (docs/auth.md). ``requires(*perms)`` on a view
answers 401 with no signed-in user and 403 when the role lacks a permission;
in auth modes ``off`` and ``clerk`` the decorator does nothing, so every
route that existed before 2026-09-27 behaves exactly as it did.

Auth mode (``auth_mode()``): ``ASSURE_AUTH_MODE`` = ``local`` | ``clerk`` |
``off``. Left unset, it is ``local`` when ``ASSURE_BOOTSTRAP_TOKEN`` is set or
an ``auth_users`` row exists (the box has been, or is about to be, set up),
``clerk`` when Clerk keys are configured and there are no local users, else
``off``. The users-exist probe is one indexed SELECT; its answer is cached per
database for ``_USERS_PROBE_TTL`` seconds and, once true, for the process —
an account is never un-created.

``register_auth_guard(app)`` — a ``before_request`` for local mode — turns the
signed cookie's ``auth_session_id`` into ``g.assure_user`` (id, email,
display_name, role, permissions) and closes every ``/api/*`` path not on the
public list to signed-out callers (401). Server-rendered pages under
``/parsing`` redirect to ``/signin?next=`` so the app is safe on :8765 alone,
not only behind the gate.
"""

from __future__ import annotations

import os
import time
from functools import wraps
from typing import Any, Callable

from flask import g, has_request_context, jsonify, redirect, request, session

ROLES = ("owner", "compliance_reviewer", "reviewer", "intake", "auditor")

PERMISSIONS = (
    "projects.read",
    "projects.create",
    "documents.upload",
    "documents.delete",
    "documents.download_original",
    "fields.accept",
    "fields.accept_compliance",
    "fields.correct",
    "fields.dispute",
    "disputes.resolve",
    "classification.override",
    "reports.replay",
    "exports.read",
    "exports.dossier",
    "settings.integrations",
    "settings.schemas",
    "settings.models",
    "team.manage",
    "audit.read",
    "compile.run",
    "sources.manage",
)

_ALL = frozenset(PERMISSIONS)

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": _ALL,
    "compliance_reviewer": _ALL - {"team.manage", "settings.integrations", "settings.schemas", "settings.models"},
    "reviewer": frozenset({
        "projects.read", "projects.create", "documents.upload", "fields.accept", "fields.correct",
        "fields.dispute", "classification.override", "exports.read", "compile.run", "sources.manage",
    }),
    "intake": frozenset({"projects.read", "documents.upload", "sources.manage"}),
    "auditor": frozenset({
        "projects.read", "exports.read", "exports.dossier", "documents.download_original", "audit.read",
    }),
}

AUTH_MODES = ("local", "clerk", "off")

SESSION_KEY = "auth_session_id"

#: Paths a signed-out caller may reach in local mode. Exact matches; the
#: sign-in flow cannot require the session it creates.
PUBLIC_API_PATHS = frozenset({
    "/health",
    "/api/health",
    "/ready",
    "/api/auth/setup-status",
    "/api/auth/setup",
    "/api/auth/login",
    "/api/auth/accept-invitation",
    "/api/auth/config",
    "/api/auth/me",
    "/api/i18n",
})

#: Server-rendered pages that redirect to /signin when signed out.
PROTECTED_PAGE_PREFIXES = ("/parsing",)

SIGNED_IN_REQUIRED = "sign in required"

DEFAULT_SESSION_HOURS = 12.0


def permissions_for(role: str | None) -> frozenset[str]:
    return ROLE_PERMISSIONS.get(str(role or ""), frozenset())


def session_hours() -> float:
    raw = (os.environ.get("ASSURE_SESSION_HOURS") or "").strip()
    try:
        hours = float(raw) if raw else DEFAULT_SESSION_HOURS
    except ValueError:
        hours = DEFAULT_SESSION_HOURS
    return hours if hours > 0 else DEFAULT_SESSION_HOURS


def bootstrap_token() -> str:
    return (os.environ.get("ASSURE_BOOTSTRAP_TOKEN") or "").strip()


# --------------------------------------------------------------------------
# Auth mode
# --------------------------------------------------------------------------

_USERS_PROBE_TTL = 5.0
_users_probe: dict[str, tuple[bool, float]] = {}


def _db_key() -> str:
    return (os.environ.get("DATABASE_PATH") or "") + "|" + (os.environ.get("DATABASE_URL") or "") + "|" + (
        os.environ.get("ASSURE_PG_SCHEMA") or ""
    )


def reset_mode_cache() -> None:
    _users_probe.clear()


def local_users_exist() -> bool:
    key = _db_key()
    cached = _users_probe.get(key)
    now = time.monotonic()
    if cached is not None and (cached[0] or cached[1] > now):
        return cached[0]
    try:
        from .db.auth_repository import any_users
    except ImportError:
        from db.auth_repository import any_users
    try:
        exists = any_users()
    except Exception:
        # No database yet (first boot before migrations) reads as "no users":
        # the mode falls back to what the environment says.
        exists = False
    _users_probe[key] = (exists, now + _USERS_PROBE_TTL)
    return exists


def auth_mode() -> str:
    explicit = (os.environ.get("ASSURE_AUTH_MODE") or "").strip().lower()
    if explicit in AUTH_MODES:
        return explicit
    if bootstrap_token() or local_users_exist():
        return "local"
    try:
        from .cloud_auth import clerk_configured
    except ImportError:
        from cloud_auth import clerk_configured
    if clerk_configured():
        return "clerk"
    return "off"


def local_mode() -> bool:
    return auth_mode() == "local"


# --------------------------------------------------------------------------
# Request identity
# --------------------------------------------------------------------------

def client_ip() -> str | None:
    """The caller's address: the first hop of ``X-Forwarded-For`` when a proxy
    is trusted (``PROXY_FIX_HOPS`` ≥ 1, the compose default), else the socket
    peer. ``ProxyFix`` already rewrote ``remote_addr`` for the trusted hop; the
    header is read here too so the audit row names the client even when the
    gate on the same box was the peer."""
    if not has_request_context():
        return None
    try:
        hops = int((os.environ.get("PROXY_FIX_HOPS") or "1").strip() or "1")
    except ValueError:
        hops = 1
    if hops >= 1:
        forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if forwarded:
            return forwarded[:64]
    return (request.remote_addr or None)


def user_agent() -> str | None:
    if not has_request_context():
        return None
    return (request.headers.get("User-Agent") or "")[:300] or None


def user_payload(user: dict[str, Any]) -> dict[str, Any]:
    """The ``g.assure_user`` shape (also what ``/api/auth/login`` returns)."""
    return {
        "id": user["id"],
        "email": user["email"],
        "display_name": user.get("display_name") or "",
        "role": user["role"],
        "permissions": sorted(permissions_for(user["role"])),
        "must_change_password": bool(user.get("must_change_password")),
    }


def current_user() -> dict[str, Any] | None:
    """The signed-in local account for this request, or None."""
    if not has_request_context():
        return None
    return g.get("assure_user")


def has_permission(perm: str) -> bool:
    """True when the caller may do ``perm``. Outside local mode everyone is
    owner-equivalent (the decorator is a no-op there for the same reason)."""
    if not local_mode():
        return True
    user = current_user()
    return bool(user) and perm in set(user.get("permissions") or ())


def deny(perm: str):
    """The 401/403 response ``requires`` would give for ``perm`` right now."""
    user = current_user()
    if not user:
        return jsonify({"ok": False, "error": SIGNED_IN_REQUIRED}), 401
    return (
        jsonify({"ok": False, "error": f"not allowed: {perm}", "required": [perm], "role": user.get("role")}),
        403,
    )


def requires(*perms: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Route decorator: every permission in ``perms`` is needed.

    Checked at request time, not at import: the mode can change between the
    first-run owner setup and the next request without a restart."""
    for perm in perms:
        if perm not in _ALL:
            raise ValueError(f"unknown permission: {perm}")

    def decorate(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any):
            if not local_mode():
                return view(*args, **kwargs)
            user = current_user()
            if not user:
                return jsonify({"ok": False, "error": SIGNED_IN_REQUIRED}), 401
            granted = set(user.get("permissions") or ())
            missing = [p for p in perms if p not in granted]
            if missing:
                return (
                    jsonify({
                        "ok": False,
                        "error": f"not allowed: {missing[0]}",
                        "required": list(perms),
                        "role": user.get("role"),
                    }),
                    403,
                )
            return view(*args, **kwargs)

        return wrapped

    return decorate


# --------------------------------------------------------------------------
# Session handling and the guard
# --------------------------------------------------------------------------

def sign_in(user: dict[str, Any]) -> dict[str, Any]:
    """Create the server-side session row and put its id in the signed cookie."""
    try:
        from .db import auth_repository as repo
    except ImportError:
        from db import auth_repository as repo
    ses = repo.create_session(user["id"], hours=session_hours(), ip=client_ip(), user_agent=user_agent())
    session.permanent = True
    session[SESSION_KEY] = ses["id"]
    g.assure_user = user_payload(user)
    g.assure_session_id = ses["id"]
    return ses


def sign_out() -> str | None:
    """Revoke the current session row and clear the cookie; returns the id."""
    try:
        from .db import auth_repository as repo
    except ImportError:
        from db import auth_repository as repo
    ses_id = session.pop(SESSION_KEY, None)
    if ses_id:
        repo.revoke_session(str(ses_id))
    g.assure_user = None
    g.assure_session_id = None
    return str(ses_id) if ses_id else None


def resolve_request_user() -> dict[str, Any] | None:
    """Fill ``g.assure_user`` from the cookie's session id (local mode)."""
    if getattr(g, "_assure_user_resolved", False):
        return g.get("assure_user")
    g._assure_user_resolved = True
    g.assure_user = None
    g.assure_session_id = None
    ses_id = session.get(SESSION_KEY)
    if not ses_id:
        return None
    try:
        from .db import auth_repository as repo
    except ImportError:
        from db import auth_repository as repo
    user = repo.resolve_session(str(ses_id), hours=session_hours())
    if not user:
        session.pop(SESSION_KEY, None)
        return None
    g.assure_user = user_payload(user)
    g.assure_session_id = str(ses_id)
    return g.assure_user


def _is_public_path(path: str) -> bool:
    p = (path or "").rstrip("/") or "/"
    return p in PUBLIC_API_PATHS or p.startswith("/static/")


def register_auth_guard(app) -> None:
    """Local-mode sign-in guard for ``/api/*`` and the protected pages."""
    from datetime import timedelta

    app.permanent_session_lifetime = timedelta(hours=session_hours())

    @app.before_request
    def _local_auth_guard():
        if not local_mode():
            return None
        path = request.path or ""
        resolve_request_user()
        if _is_public_path(path) or request.method == "OPTIONS":
            return None
        try:
            from .service_auth import is_service_api_request
        except ImportError:
            from service_auth import is_service_api_request
        if is_service_api_request(path):
            # Carries its own factor (service token, checked by web._cloud_login).
            return None
        if g.get("assure_user"):
            return None
        if path.startswith("/api/"):
            return jsonify({"ok": False, "error": SIGNED_IN_REQUIRED}), 401
        if any(path == prefix or path.startswith(prefix + "/") for prefix in PROTECTED_PAGE_PREFIXES):
            import urllib.parse

            nxt = path + ("?" + request.query_string.decode() if request.query_string else "")
            return redirect("/signin?" + urllib.parse.urlencode({"next": nxt}))
        return None
