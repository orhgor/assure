"""Team management and the merged audit feed (2026-09-27, docs/auth.md).

``/api/team/*`` needs ``team.manage`` (the owner); ``/api/audit`` and a user's
activity page need ``audit.read`` (owner, compliance reviewer, auditor). The
invitation token is returned once in the creation response — only its hash is
stored — and is mailed through Resend when ``RESEND_API_KEY`` is configured;
a box without mail still gets the link (``emailed: false``), never an error.

Two refusals are 409 rather than 403 because the caller *is* allowed to manage
the team; the state forbids the change: demoting or disabling the last active
owner (the box would have nobody left who can manage it) and changing one's
own role (an owner cannot lock themselves out by accident).
"""

from __future__ import annotations

import html
import logging
import os
import secrets
import sqlite3
from typing import Any

from flask import current_app, g, jsonify, request

try:
    from .. import rbac
    from ..db import auth_repository as repo
    from ..db import parsure_repository as parsure
    from ..rbac import requires
except ImportError:
    import rbac  # type: ignore
    from db import auth_repository as repo  # type: ignore
    from db import parsure_repository as parsure  # type: ignore
    from rbac import requires  # type: ignore

log = logging.getLogger(__name__)

_USER_NOT_FOUND = "User not found."


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


def _resend_key() -> str:
    key = ""
    try:
        key = str(current_app.config.get("RESEND_API_KEY") or "")
    except RuntimeError:
        key = ""
    return key.strip() or (os.environ.get("RESEND_API_KEY") or "").strip()


def _send_invitation_email(*, to: str, accept_url: str, role: str, org_name: str, inviter: str) -> bool:
    """Mail the invitation link through Resend; False when not configured or
    when sending failed (logged). The request never fails on mail."""
    key = _resend_key()
    if not key:
        return False
    try:
        import resend

        resend.api_key = key
        sender = (current_app.config.get("RESEND_FROM_EMAIL") or os.environ.get("RESEND_FROM_EMAIL")
                  or "notifications@getassureai.com")
        org = html.escape(org_name or "Assure")
        resend.Emails.send({
            "from": sender,
            "to": [to],
            "subject": f"You are invited to {org} on Assure",
            "html": (
                f"<p>{html.escape(inviter or 'An owner')} invited you to <strong>{org}</strong> as "
                f"<strong>{html.escape(role)}</strong>.</p>"
                f"<p><a href=\"{html.escape(accept_url)}\">Accept the invitation</a> and choose your password. "
                f"The link is valid for {repo.INVITATION_DAYS} days.</p>"
            ),
        })
        return True
    except Exception as exc:  # mail is best-effort by contract
        log.warning("invitation e-mail to %s not sent: %s", to, exc.__class__.__name__)
        return False


def _accept_url(token: str) -> str:
    return f"/accept?token={token}"


def _public_base_url() -> str:
    """Absolute origin for the e-mail link: ``ASSURE_PUBLIC_URL`` when set (the
    gate's address, not this process's), else the request's own origin."""
    base = (os.environ.get("ASSURE_PUBLIC_URL") or "").strip().rstrip("/")
    if base:
        return base
    return (request.host_url or "").rstrip("/")


def _merged_events(
    *,
    project_id: str | None,
    actor_id: str | None,
    event_type: str | None,
    since: str | None,
    limit: int,
    include_auth: bool = True,
    include_parsure: bool = True,
) -> list[dict[str, Any]]:
    """Parsure review events and account events in one list, newest first."""
    events: list[dict[str, Any]] = []
    if include_parsure and not (event_type and event_type in repo.AUTH_EVENT_TYPES and event_type not in parsure.EVENT_TYPES):
        for e in parsure.query_events(project_id=project_id, actor_id=actor_id, event_type=event_type, since=since, limit=limit):
            events.append({
                "id": e["id"],
                "source": "parsure",
                "event_type": e["event_type"],
                "project_id": e.get("project_id"),
                "report_id": e.get("report_id"),
                "field_name": e.get("field_name"),
                "actor": e.get("actor"),
                "actor_id": e.get("actor_id"),
                "actor_role": e.get("actor_role"),
                "ip": e.get("ip"),
                "payload": e.get("payload") or {},
                "created_at": e.get("created_at"),
            })
    if include_auth and not project_id and not (event_type and event_type not in repo.AUTH_EVENT_TYPES):
        names = {u["id"]: (u.get("display_name") or u["email"]) for u in repo.list_users()}
        for e in repo.list_auth_events(actor_id=actor_id, event_type=event_type, since=since, limit=limit):
            events.append({
                "id": e["id"],
                "source": "auth",
                "event_type": e["event_type"],
                "project_id": None,
                "report_id": None,
                "field_name": None,
                "actor": names.get(e.get("actor_id") or "", None),
                "actor_id": e.get("actor_id"),
                "actor_role": e.get("actor_role"),
                "subject_user_id": e.get("subject_user_id"),
                "subject": names.get(e.get("subject_user_id") or "", None),
                "ip": e.get("ip"),
                "payload": e.get("payload") or {},
                "created_at": e.get("created_at"),
            })
    events.sort(key=lambda e: (str(e.get("created_at") or ""), int(e.get("id") or 0)), reverse=True)
    return events[:limit]


def _limit(default: int = 200) -> int:
    try:
        return max(1, min(int(request.args.get("limit") or default), 2000))
    except ValueError:
        return default


def register_team_routes(app) -> None:
    # ---------------------------------------------------------------- users
    @app.get("/api/team/users")
    @requires("team.manage")
    def team_users():
        return jsonify({"ok": True, "users": [repo.public_user(u) for u in repo.list_users()]})

    @app.patch("/api/team/users/<user_id>")
    @requires("team.manage")
    def team_update_user(user_id: str):
        target = repo.get_user(user_id)
        if not target:
            return jsonify({"ok": False, "error": _USER_NOT_FOUND}), 404
        body = _body()
        me = rbac.current_user() or {}
        changes: dict[str, Any] = {}
        if "role" in body:
            role = str(body.get("role") or "").strip()
            if role not in rbac.ROLES:
                return jsonify({"ok": False, "error": f"role must be one of: {', '.join(rbac.ROLES)}"}), 400
            if role != target["role"]:
                changes["role"] = role
        if "status" in body:
            status = str(body.get("status") or "").strip()
            if status not in ("active", "disabled"):
                return jsonify({"ok": False, "error": "status must be active or disabled"}), 400
            if target["status"] == "invited" and status == "active":
                return jsonify({"ok": False, "error": "An invited user becomes active by accepting the invitation."}), 409
            if status != target["status"]:
                changes["status"] = status
        if "display_name" in body:
            name = str(body.get("display_name") or "").strip()[:120]
            if name != (target.get("display_name") or ""):
                changes["display_name"] = name or None
        if target["id"] == me.get("id") and "role" in changes:
            return jsonify({"ok": False, "error": "You cannot change your own role."}), 409
        loses_owner = target["role"] == "owner" and target["status"] == "active" and (
            changes.get("role", "owner") != "owner" or changes.get("status", "active") != "active"
        )
        if loses_owner and repo.count_active_owners() <= 1:
            return jsonify({"ok": False, "error": "This is the last active owner; promote another owner first."}), 409
        if not changes:
            return jsonify({"ok": True, "user": repo.public_user(target), "changed": []})
        updated = repo.update_user(user_id, **changes)
        actor = _actor_fields()
        if "role" in changes:
            repo.log_auth_event("role_changed", subject_user_id=user_id, **actor,
                                payload={"from": target["role"], "to": changes["role"]})
        if "status" in changes:
            if changes["status"] == "disabled":
                repo.revoke_user_sessions(user_id)
            repo.log_auth_event("status_changed", subject_user_id=user_id, **actor,
                                payload={"from": target["status"], "to": changes["status"]})
        if "display_name" in changes:
            repo.log_auth_event("display_name_changed", subject_user_id=user_id, **actor,
                                payload={"from": target.get("display_name") or "", "to": changes["display_name"] or ""})
        return jsonify({"ok": True, "user": repo.public_user(updated), "changed": sorted(changes)})

    @app.post("/api/team/users/<user_id>/reset-password")
    @requires("team.manage")
    def team_reset_password(user_id: str):
        target = repo.get_user(user_id)
        if not target:
            return jsonify({"ok": False, "error": _USER_NOT_FOUND}), 404
        if target["status"] == "invited":
            return jsonify({"ok": False, "error": "This user has not accepted the invitation yet; re-send it instead."}), 409
        temporary = secrets.token_urlsafe(12)
        repo.set_password(user_id, temporary, must_change=True)
        revoked = repo.revoke_user_sessions(user_id)
        repo.log_auth_event("password_reset", subject_user_id=user_id, **_actor_fields(),
                            payload={"sessions_revoked": revoked})
        return jsonify({"ok": True, "user": repo.public_user(repo.get_user(user_id)), "temporary_password": temporary,
                        "must_change_password": True, "sessions_revoked": revoked})

    # ---------------------------------------------------------- invitations
    @app.post("/api/team/invitations")
    @requires("team.manage")
    def team_invite():
        body = _body()
        email = repo.normalize_email(body.get("email"))
        role = str(body.get("role") or "").strip()
        if not email or "@" not in email:
            return jsonify({"ok": False, "error": "A valid e-mail address is required."}), 400
        if role not in rbac.ROLES:
            return jsonify({"ok": False, "error": f"role must be one of: {', '.join(rbac.ROLES)}"}), 400
        me = rbac.current_user() or {}
        try:
            inv, token = repo.create_invitation(email=email, role=role, created_by=me.get("id"))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        except sqlite3.IntegrityError:
            return jsonify({"ok": False, "error": "An account with this e-mail already exists."}), 409
        accept_url = _accept_url(token)
        emailed = _send_invitation_email(
            to=email, accept_url=_public_base_url() + accept_url, role=role,
            org_name=str(repo.get_setting("org_name", "") or ""), inviter=me.get("display_name") or me.get("email") or "",
        )
        repo.log_auth_event("invited", subject_user_id=(repo.get_user_by_email(email) or {}).get("id"), **_actor_fields(),
                            payload={"invitation_id": inv["id"], "email": email, "role": role, "emailed": emailed})
        return jsonify({
            "ok": True,
            "invitation": {"id": inv["id"], "email": inv["email"], "role": inv["role"], "expires_at": inv["expires_at"]},
            "token": token,
            "accept_url": accept_url,
            "emailed": emailed,
        })

    @app.get("/api/team/invitations")
    @requires("team.manage")
    def team_invitations():
        include_accepted = (request.args.get("all") or "").strip().lower() in ("1", "true", "yes")
        return jsonify({"ok": True, "invitations": [repo.public_invitation(i) for i in repo.list_invitations(include_accepted=include_accepted)]})

    @app.delete("/api/team/invitations/<inv_id>")
    @requires("team.manage")
    def team_delete_invitation(inv_id: str):
        inv = repo.get_invitation(inv_id)
        if not inv or inv.get("accepted_at"):
            return jsonify({"ok": False, "error": "Invitation not found."}), 404
        repo.delete_invitation(inv_id)
        repo.log_auth_event("invitation_revoked", **_actor_fields(),
                            payload={"invitation_id": inv_id, "email": inv["email"], "role": inv["role"]})
        return jsonify({"ok": True, "deleted": inv_id})

    # ------------------------------------------------------------- sessions
    @app.get("/api/team/sessions")
    @requires("team.manage")
    def team_sessions():
        names = {u["id"]: u for u in repo.list_users()}
        current = g.get("assure_session_id")
        out = []
        for s in repo.list_sessions(active_only=True):
            u = names.get(s["user_id"]) or {}
            out.append({
                **s,
                "email": u.get("email"),
                "display_name": u.get("display_name") or "",
                "role": u.get("role"),
                "current": s["id"] == current,
            })
        return jsonify({"ok": True, "sessions": out})

    @app.delete("/api/team/sessions/<ses_id>")
    @requires("team.manage")
    def team_revoke_session(ses_id: str):
        ses = repo.get_session(ses_id)
        if not ses:
            return jsonify({"ok": False, "error": "Session not found."}), 404
        revoked = repo.revoke_session(ses_id)
        repo.log_auth_event("session_revoked", subject_user_id=ses["user_id"], **_actor_fields(),
                            payload={"session_id": ses_id, "already_revoked": not revoked})
        return jsonify({"ok": True, "revoked": True, "session_id": ses_id})

    # ---------------------------------------------------------------- audit
    @app.get("/api/audit")
    @requires("audit.read")
    def audit_feed():
        project_id = (request.args.get("project_id") or "").strip() or None
        actor_id = (request.args.get("actor_id") or "").strip() or None
        event_type = (request.args.get("event_type") or "").strip() or None
        since = (request.args.get("since") or "").strip() or None
        limit = _limit()
        events = _merged_events(project_id=project_id, actor_id=actor_id, event_type=event_type, since=since, limit=limit)
        return jsonify({
            "ok": True,
            "events": events,
            "event_types": {"parsure": list(parsure.EVENT_TYPES), "auth": list(repo.AUTH_EVENT_TYPES)},
        })

    @app.get("/api/team/users/<user_id>/activity")
    @requires("audit.read")
    def team_user_activity(user_id: str):
        target = repo.get_user(user_id)
        if not target:
            return jsonify({"ok": False, "error": _USER_NOT_FOUND}), 404
        since = (request.args.get("since") or "").strip() or None
        events = _merged_events(project_id=None, actor_id=user_id, event_type=None, since=since, limit=_limit())
        return jsonify({"ok": True, "user": repo.public_user(target), "events": events})
