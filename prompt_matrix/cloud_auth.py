"""Optional Clerk login for cloud mode. Keys and prompts stay local.

Auth is off unless both Clerk keys are set. ASSURE_EDITION=self-hosted
skips it even when keys exist. No Clerk SDK dependency: one verify call
to Clerk's Backend API, then the user id lives in the Flask session.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from flask import jsonify, redirect, request, session

CLERK_API = "https://api.clerk.com/v1"
PROTECTED_HTML = frozenset({"/", "/compose", "/connect"})
PUBLIC_API = frozenset(
    {
        "/api/health",
        "/api/i18n",
        "/api/auth/config",
        "/api/auth/session",
        "/api/auth/logout",
        "/api/auth/me",
        "/api/webhook/stripe",
    }
)
PUBLIC_HTML = frozenset({"/signin", "/signup", "/signout", "/pricing", "/privacy", "/about"})


class AuthError(ValueError):
    pass


def clerk_publishable_key() -> str:
    return (
        (os.environ.get("CLERK_PUBLISHABLE_KEY") or "").strip()
        or (os.environ.get("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY") or "").strip()
    )


def clerk_secret_key() -> str:
    return (os.environ.get("CLERK_SECRET_KEY") or "").strip()


def clerk_configured() -> bool:
    return bool(clerk_publishable_key() and clerk_secret_key())


def is_self_hosted() -> bool:
    raw = (os.environ.get("ASSURE_EDITION") or os.environ.get("PEM_EDITION") or "").strip().lower()
    return raw.replace("_", "-") in {"self-hosted", "selfhosted", "self-host"}


def auth_required() -> bool:
    if is_self_hosted():
        return False
    return clerk_configured()


def template_state(*, include_pk: bool = False) -> dict[str, Any]:
    user_id = session.get("clerk_user_id")
    state = {
        "configured": clerk_configured(),
        "required": auth_required(),
        "self_hosted": is_self_hosted(),
        "signed_in": bool(user_id),
        "user_id": user_id or "",
        "email": session.get("clerk_email") or "",
        "publishable_key": "",
    }
    if include_pk and clerk_configured() and not is_self_hosted():
        state["publishable_key"] = clerk_publishable_key()
    return state


def current_user_id() -> str | None:
    raw = session.get("clerk_user_id")
    return str(raw) if raw else None


def clear_user() -> None:
    session.pop("clerk_user_id", None)
    session.pop("clerk_email", None)


def delete_clerk_user(user_id: str) -> None:
    if not user_id or not clerk_configured():
        return
    _clerk_json("DELETE", "/users/" + urllib.parse.quote(user_id, safe=""))


def remember_user(*, user_id: str, email: str = "") -> None:
    session["clerk_user_id"] = user_id
    if email:
        session["clerk_email"] = email


def jwt_payload(token: str) -> dict[str, Any]:
    parts = (token or "").split(".")
    if len(parts) != 3:
        raise AuthError("not a session token")
    pad = "=" * (-len(parts[1]) % 4)
    try:
        raw = base64.urlsafe_b64decode(parts[1] + pad)
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthError("not a session token") from exc
    if not isinstance(data, dict):
        raise AuthError("not a session token")
    return data


def _clerk_json(method: str, path: str, body: dict | None = None) -> dict[str, Any]:
    secret = clerk_secret_key()
    if not secret:
        raise AuthError("cloud login is not set up")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        CLERK_API + path,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + secret,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise AuthError("session not valid") from exc
    except urllib.error.URLError as exc:
        raise AuthError("could not reach Clerk") from exc
    if not raw:
        return {}
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


def verify_session_token(token: str) -> dict[str, str]:
    payload = jwt_payload(token)
    sid = payload.get("sid")
    if not isinstance(sid, str) or not sid:
        raise AuthError("missing session id")
    path = "/sessions/" + urllib.parse.quote(sid, safe="") + "/verify"
    data = _clerk_json("POST", path, {"token": token})
    user_id = data.get("user_id") or payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise AuthError("missing user id")
    email = _user_email(user_id)
    return {"id": user_id, "email": email}


def _user_email(user_id: str) -> str:
    try:
        data = _clerk_json("GET", "/users/" + urllib.parse.quote(user_id, safe=""))
    except AuthError:
        return ""
    addrs = data.get("email_addresses") or []
    if isinstance(addrs, list):
        for item in addrs:
            if isinstance(item, dict) and item.get("email_address"):
                return str(item["email_address"])
    return ""


def safe_next(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw.startswith("/") or raw.startswith("//"):
        return "/"
    return raw


def protect_request():
    """Redirect HTML, or 401 JSON, when cloud login is on and the session is empty."""
    path = request.path
    if path.startswith("/static/") or path == "/api/webhook/stripe":
        return None
    if current_user_id():
        try:
            from .cloud_billing import bind_request_tier
        except ImportError:
            from cloud_billing import bind_request_tier
        try:
            bind_request_tier()
        except Exception:
            try:
                from .editions import set_request_plan
            except ImportError:
                from editions import set_request_plan
            set_request_plan("free")
    if not auth_required():
        return None
    if path in PUBLIC_HTML or path in PUBLIC_API:
        return None
    if current_user_id():
        return None
    if path.startswith("/api/"):
        return jsonify({"error": "Sign in to use Compose."}), 401
    target = "/signin"
    if path not in {"/", "/compose"}:
        nxt = path
        if request.query_string:
            nxt = path + "?" + request.query_string.decode()
        target = "/signin?" + urllib.parse.urlencode({"next": nxt})
    return redirect(target)
