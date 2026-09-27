"""Sign-in routes for local user management (2026-09-27, docs/auth.md).

``/api/auth/setup-status``, ``/setup``, ``/login``, ``/accept-invitation`` are
public (``rbac.PUBLIC_API_PATHS``): a session cannot be required to create
one. ``/api/auth/config``, ``/me`` and ``/logout`` existed for Clerk in
``web.py`` and moved here so one handler answers each URL in every mode —
outside local mode they keep the Clerk shape they had, plus ``mode``.

Status codes are the contract the shell builds against: 400 a rule broken (the
rule is spelled out), 401 wrong credentials (one generic message, so an
address cannot be probed), 403 the account is disabled or setup is refused,
423 the account is locked after five failures. Nothing here logs a password,
and the audit rows record the account, address and user agent, never a secret.
"""

from __future__ import annotations

import hmac
import logging
import sqlite3
from typing import Any

from flask import g, jsonify, request, session

try:
    from .. import rbac
    from ..db import auth_repository as repo
except ImportError:
    import rbac  # type: ignore
    from db import auth_repository as repo  # type: ignore

log = logging.getLogger(__name__)

_BAD_CREDENTIALS = "Invalid e-mail or password."


def _body() -> dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _actor_fields() -> dict[str, Any]:
    user = rbac.current_user() or {}
    return {
        "actor_id": user.get("id"),
        "actor_role": user.get("role"),
        "ip": rbac.client_ip(),
        "user_agent": rbac.user_agent(),
    }


def _needs_owner() -> bool:
    return rbac.auth_mode() == "local" and not rbac.local_users_exist()


def _org_name() -> str:
    try:
        return str(repo.get_setting("org_name", "") or "")
    except Exception:
        return ""


def _me_payload() -> dict[str, Any]:
    mode = rbac.auth_mode()
    user = rbac.current_user() if mode == "local" else None
    if user:
        return {
            "ok": True,
            "user_id": user["id"],
            "email": user["email"],
            "display_name": user.get("display_name") or "",
            "role": user["role"],
            "permissions": list(user.get("permissions") or []),
            "mode": mode,
            "must_change_password": bool(user.get("must_change_password")),
        }
    if mode == "clerk":
        try:
            from ..cloud_auth import current_user_id
        except ImportError:
            from cloud_auth import current_user_id
        return {
            "ok": True,
            "user_id": current_user_id() or "",
            "email": session.get("clerk_email") or "",
            "mode": mode,
            "needs_owner": False,
        }
    return {"ok": True, "user_id": "", "mode": mode, "needs_owner": _needs_owner()}


def register_auth_routes(app) -> None:
    @app.get("/api/auth/setup-status")
    def auth_setup_status():
        return jsonify({
            "ok": True,
            "mode": rbac.auth_mode(),
            "needs_owner": _needs_owner(),
            "org_name": _org_name(),
            "bootstrap_required": True,
        })

    @app.post("/api/auth/setup")
    def auth_setup():
        """First run: the bootstrap token from ``.env`` (printed by gen-env.sh)
        creates the owner. Refused for good once any account exists — a second
        owner comes from an invitation, not from the token."""
        body = _body()
        if rbac.auth_mode() != "local":
            return jsonify({"ok": False, "error": "Local accounts are not enabled (ASSURE_AUTH_MODE)."}), 403
        if rbac.local_users_exist():
            return jsonify({"ok": False, "error": "Setup is already complete; sign in or ask an owner for an invitation."}), 403
        expected = rbac.bootstrap_token()
        presented = str(body.get("bootstrap_token") or "")
        if not expected or not hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8")):
            repo.log_auth_event("login_failed", ip=rbac.client_ip(), user_agent=rbac.user_agent(),
                                payload={"reason": "bootstrap_token", "email": repo.normalize_email(body.get("email"))})
            return jsonify({"ok": False, "error": "Bootstrap token does not match ASSURE_BOOTSTRAP_TOKEN."}), 403
        email = repo.normalize_email(body.get("email"))
        if not email or "@" not in email:
            return jsonify({"ok": False, "error": "A valid e-mail address is required."}), 400
        problem = repo.password_policy_error(body.get("password"))
        if problem:
            return jsonify({"ok": False, "error": problem}), 400
        org_name = str(body.get("org_name") or "").strip()[:120]
        display_name = str(body.get("display_name") or "").strip()[:120] or None
        try:
            user = repo.create_user(email=email, role="owner", display_name=display_name,
                                    password=str(body["password"]), created_by="setup")
        except sqlite3.IntegrityError:
            return jsonify({"ok": False, "error": "Setup is already complete."}), 403
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        repo.set_setting("org_name", org_name)
        repo.set_setting("setup_completed_at", repo._now())
        rbac.reset_mode_cache()
        rbac.sign_in(user)
        repo.log_auth_event("setup", subject_user_id=user["id"], **_actor_fields(),
                            payload={"org_name": org_name, "email": email})
        return jsonify({"ok": True, "user": rbac.user_payload(user), "org_name": org_name})

    @app.post("/api/auth/login")
    def auth_login():
        if rbac.auth_mode() != "local":
            return jsonify({"ok": False, "error": "Local sign-in is not enabled (ASSURE_AUTH_MODE)."}), 403
        body = _body()
        email = repo.normalize_email(body.get("email"))
        password = body.get("password")
        ip, ua = rbac.client_ip(), rbac.user_agent()
        user = repo.get_user_by_email(email) if email else None
        if not user or not user.get("password_hash"):
            repo.log_auth_event("login_failed", ip=ip, user_agent=ua, payload={"email": email, "reason": "unknown"})
            return jsonify({"ok": False, "error": _BAD_CREDENTIALS}), 401
        if user["status"] == "disabled":
            repo.log_auth_event("login_failed", subject_user_id=user["id"], ip=ip, user_agent=ua,
                                payload={"email": email, "reason": "disabled"})
            return jsonify({"ok": False, "error": "This account is disabled."}), 403
        lock = repo.locked_until(user)
        if lock:
            repo.log_auth_event("login_failed", subject_user_id=user["id"], ip=ip, user_agent=ua,
                                payload={"email": email, "reason": "locked"})
            minutes = max(1, int((lock - repo._now_dt()).total_seconds() // 60) + 1)
            return jsonify({"ok": False, "error": f"Account locked after too many failed attempts. Try again in {minutes} min.",
                            "locked_until": lock.strftime(repo._TS)}), 423
        if user["status"] != "active" or not repo.verify_password(user, password if isinstance(password, str) else ""):
            failures, locked = repo.record_login_failure(user["id"])
            repo.log_auth_event("login_failed", subject_user_id=user["id"], ip=ip, user_agent=ua,
                                payload={"email": email, "reason": "password", "failed_logins": failures})
            if locked:
                repo.revoke_user_sessions(user["id"])
                repo.log_auth_event("locked", subject_user_id=user["id"], ip=ip, user_agent=ua,
                                    payload={"email": email, "locked_until": locked, "minutes": repo.LOCKOUT_MINUTES})
                return jsonify({"ok": False, "error": f"Account locked after {repo.LOCKOUT_THRESHOLD} failed attempts. "
                                                       f"Try again in {repo.LOCKOUT_MINUTES} min.",
                                "locked_until": locked}), 423
            return jsonify({"ok": False, "error": _BAD_CREDENTIALS}), 401
        repo.record_login_success(user["id"])
        user = repo.get_user(user["id"]) or user
        rbac.sign_in(user)
        repo.log_auth_event("login", subject_user_id=user["id"], **_actor_fields(), payload={"email": email})
        return jsonify({"ok": True, "user": rbac.user_payload(user)})

    @app.post("/api/auth/logout")
    def auth_logout():
        mode = rbac.auth_mode()
        if mode == "local":
            rbac.resolve_request_user()
            actor = _actor_fields()
            user = rbac.current_user()
            ses_id = rbac.sign_out()
            if user:
                repo.log_auth_event("logout", subject_user_id=user["id"], **actor, payload={"session_id": ses_id})
            return jsonify({"ok": True})
        try:
            from ..cloud_auth import clear_user
        except ImportError:
            from cloud_auth import clear_user
        clear_user()
        return jsonify({"ok": True})

    @app.get("/api/auth/me")
    def auth_me():
        if rbac.auth_mode() == "local":
            rbac.resolve_request_user()
        return jsonify(_me_payload())

    @app.get("/api/auth/config")
    def auth_config():
        try:
            from ..cloud_auth import auth_required, clerk_configured, clerk_only_enabled, current_user_id, is_self_hosted
        except ImportError:
            from cloud_auth import auth_required, clerk_configured, clerk_only_enabled, current_user_id, is_self_hosted
        mode = rbac.auth_mode()
        if mode == "local":
            rbac.resolve_request_user()
            signed_in = bool(rbac.current_user())
            required = True
        else:
            signed_in = bool(current_user_id())
            required = auth_required()
        return jsonify({
            "required": required,
            "configured": clerk_configured() if mode != "local" else True,
            "self_hosted": is_self_hosted(),
            "signed_in": signed_in,
            # The edge reads this to decide whether shell documents need a
            # session, so the flag lives in the app's env only.
            "clerk_only": clerk_only_enabled(),
            "mode": mode,
            "needs_owner": _needs_owner(),
        })

    @app.post("/api/auth/password")
    def auth_password():
        if rbac.auth_mode() != "local":
            return jsonify({"ok": False, "error": "Local sign-in is not enabled (ASSURE_AUTH_MODE)."}), 403
        user = rbac.current_user()
        if not user:
            return jsonify({"ok": False, "error": rbac.SIGNED_IN_REQUIRED}), 401
        body = _body()
        row = repo.get_user(user["id"])
        if not row or not repo.verify_password(row, str(body.get("current_password") or "")):
            return jsonify({"ok": False, "error": "Current password is incorrect."}), 401
        new_password = body.get("new_password")
        problem = repo.password_policy_error(new_password)
        if problem:
            return jsonify({"ok": False, "error": problem}), 400
        if new_password == body.get("current_password"):
            return jsonify({"ok": False, "error": "New password must differ from the current one."}), 400
        repo.set_password(user["id"], str(new_password), must_change=False)
        # Other devices signed in with the old password lose their session.
        revoked = repo.revoke_user_sessions(user["id"], except_session=g.get("assure_session_id"))
        g.assure_user = rbac.user_payload(repo.get_user(user["id"]) or row)
        repo.log_auth_event("password_changed", subject_user_id=user["id"], **_actor_fields(),
                            payload={"sessions_revoked": revoked})
        return jsonify({"ok": True, "must_change_password": False, "sessions_revoked": revoked})

    @app.post("/api/auth/accept-invitation")
    def auth_accept_invitation():
        if rbac.auth_mode() != "local":
            return jsonify({"ok": False, "error": "Local sign-in is not enabled (ASSURE_AUTH_MODE)."}), 403
        body = _body()
        inv = repo.find_invitation_by_token(str(body.get("token") or ""))
        if not inv:
            return jsonify({"ok": False, "error": "Invitation not found or expired."}), 404
        problem = repo.password_policy_error(body.get("password"))
        if problem:
            return jsonify({"ok": False, "error": problem}), 400
        display_name = str(body.get("display_name") or "").strip()[:120] or None
        try:
            user = repo.accept_invitation(inv, display_name=display_name, password=str(body["password"]))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        rbac.reset_mode_cache()
        rbac.sign_in(user)
        repo.log_auth_event("invitation_accepted", subject_user_id=user["id"], **_actor_fields(),
                            payload={"invitation_id": inv["id"], "email": user["email"], "role": user["role"]})
        return jsonify({"ok": True, "user": rbac.user_payload(user)})
