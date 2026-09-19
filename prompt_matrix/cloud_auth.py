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
from functools import wraps
from typing import Any

from flask import jsonify, redirect, request, session

CLERK_API = "https://api.clerk.com/v1"
PROTECTED_HTML = frozenset({"/", "/compose"})
PUBLIC_API = frozenset(
    {
        "/health",
        "/api/health",
        "/api/status",
        "/api/keys",
        "/api/i18n",
        "/api/auth/config",
        "/api/auth/session",
        "/api/auth/logout",
        "/api/auth/me",
        "/api/webhook/stripe",
        "/api/webhooks/stripe",
        "/api/waitlist",
        "/api/sandbox/verify",
    }
)
PUBLIC_HTML = frozenset(
    {
        "/",
        "/architecture",
        "/signin",
        "/signup",
        "/signout",
        "/pricing",
        "/privacy",
        "/about",
        "/terms",
        "/connect",
    }
)


class AuthError(ValueError):
    pass


def clerk_publishable_key() -> str:
    return (os.environ.get("CLERK_PUBLISHABLE_KEY") or "").strip() or (
        os.environ.get("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY") or ""
    ).strip()


def clerk_secret_key() -> str:
    return (os.environ.get("CLERK_SECRET_KEY") or "").strip()


def clerk_configured() -> bool:
    return bool(clerk_publishable_key() and clerk_secret_key())


def is_self_hosted() -> bool:
    raw = (os.environ.get("ASSURE_EDITION") or os.environ.get("PEM_EDITION") or "").strip().lower()
    return raw.replace("_", "-") in {"self-hosted", "selfhosted", "self-host"}


def is_production_env() -> bool:
    return (os.environ.get("ENVIRONMENT") or "").strip().lower() == "production"


def is_loopback_request() -> bool:
    """True for local dev clients so Clerk never locks out loopback testing."""
    addr = (request.remote_addr or "").strip().lower()
    if addr in {"127.0.0.1", "::1", "localhost"}:
        return True
    host = (request.host or "").split(":")[0].strip().lower()
    return host in {"127.0.0.1", "localhost"}


def require_clerk_login() -> bool:
    """Honour ASSURE_REQUIRE_LOGIN=false for local/docker overrides."""
    if not auth_required():
        return False
    raw = (os.environ.get("ASSURE_REQUIRE_LOGIN") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def loopback_api_bypass() -> bool:
    """Allow local API scripts on loopback without a Clerk session (non-production only)."""
    return (
        not is_production_env()
        and is_loopback_request()
        and (request.path or "").startswith("/api/")
    )


def auth_required() -> bool:
    if is_self_hosted():
        return False
    return clerk_configured()


def template_state(*, include_pk: bool = False) -> dict[str, Any]:
    user_id = session.get("clerk_user_id")
    state = {
        "configured": clerk_configured(),
        "required": require_clerk_login() and not loopback_api_bypass(),
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
    try:
        from .credit_guard import ensure_wallet
    except ImportError:
        from credit_guard import ensure_wallet
    try:
        ensure_wallet(user_id)
    except Exception:
        pass


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


def _bearer_token() -> str | None:
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        return token or None
    return None


def _bind_request_tier() -> None:
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


def _try_bearer_session() -> bool:
    token = _bearer_token()
    if not token:
        return False
    try:
        user = verify_session_token(token)
    except AuthError:
        return False
    remember_user(user_id=user["id"], email=user.get("email") or "")
    _bind_request_tier()
    return True


def protect_request():
    """Redirect HTML, or 401 JSON, when cloud login is on and the session is empty."""
    path = request.path
    if path.startswith("/static/") or path.startswith("/workbench/"):
        return None
    if path in PUBLIC_HTML or path in PUBLIC_API:
    if loopback_api_bypass():
        return None
    if current_user_id():
        _bind_request_tier()
    if not require_clerk_login():
        return None
    if current_user_id():
        return None
    if is_production_env() and path.startswith("/api/"):
        if _try_bearer_session():
            return None
        return jsonify({"error": "Sign in to use Compose."}), 401
    if path.startswith("/api/"):
        return jsonify({"error": "Sign in to use Compose."}), 401
    target = "/signin"
    if path not in {"/", "/compose"}:
        nxt = path
        if request.query_string:
            nxt = path + "?" + request.query_string.decode()
        target = "/signin?" + urllib.parse.urlencode({"next": nxt})
    return redirect(target)


def login_required(view):
    """Gate HTML and JSON when Clerk is configured. No-op without a publishable key."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        blocked = protect_request()
        if blocked is not None:
            return blocked
        return view(*args, **kwargs)

    return wrapped
