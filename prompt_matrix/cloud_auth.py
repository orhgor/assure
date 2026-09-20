"""Optional Clerk login for cloud mode. Keys and prompts stay local.

Auth is off unless both Clerk keys are set. ASSURE_EDITION=self-hosted
skips it even when keys exist. No Clerk SDK dependency: one verify call
to Clerk's Backend API, then the user id lives in the Flask session.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import wraps
from typing import Any

from flask import jsonify, redirect, request, session

CLERK_API = "https://api.clerk.com/v1"
CLERK_USER_AGENT = "assure-cloud-auth/1.0"
_JWKS_TTL_SECONDS = 21600
_JWT_LEEWAY_SECONDS = 120
_JWKS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
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
        # Edge-worker ingest: the Cloudflare Worker posts extracted PDF text here
        # with X-Assure-Worker-Secret. There is no browser and no session on that
        # path, so it carries its own factor and must not require a Clerk session.
        "/api/substrate",
    }
)
PUBLIC_HTML = frozenset(
    {
        # "/" is deliberately absent: it is the product's front door and belongs
        # only to PROTECTED_HTML. Listed here it would win (PUBLIC_* is checked
        # first) and make the shell anonymously reachable.
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


# Two lists answer two different questions, and the order they are consulted in is
# the whole story:
#
#   PUBLIC_HTML / PUBLIC_API — must answer *without* a session. The sign-in flow
#       itself lives here (/signin, /api/auth/*, since a session cannot be required
#       to create one), with health probes, provider webhooks, and the
#       worker-secret-authenticated substrate ingest.
#   PROTECTED_HTML — the product's own documents. These take the /signin redirect
#       when Clerk is configured; "/" and "/compose" are the doors a signed-out
#       visitor must not walk through.
#
# protect_request() consults PUBLIC_* first, so a path in both lists is silently
# public. That is a contradiction rather than a preference, so it fails at startup.
def assert_route_lists_disjoint() -> None:
    """Fail fast when a path is both public and protected, naming the path."""
    clashes = sorted(PROTECTED_HTML & (PUBLIC_HTML | PUBLIC_API))
    if clashes:
        raise RuntimeError(
            "auth route lists disagree: "
            + ", ".join(clashes)
            + " (present in PROTECTED_HTML and in a PUBLIC_* list). PUBLIC_* is checked "
            "first, so the public entry wins and the path answers without a session. "
            "Remove it from PUBLIC_HTML/PUBLIC_API, or from PROTECTED_HTML if it really is public."
        )


assert_route_lists_disjoint()


class AuthError(ValueError):
    pass


def clerk_publishable_key() -> str:
    return (os.environ.get("CLERK_PUBLISHABLE_KEY") or "").strip() or (
        os.environ.get("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY") or ""
    ).strip()


def clerk_secret_key() -> str:
    return (os.environ.get("CLERK_SECRET_KEY") or "").strip()


def instance_frontend_api() -> str:
    """The instance's Frontend API origin, decoded from our own publishable key.

    A publishable key is `pk_test_<base64(frontend-api + "$")>`. The issuer of an
    incoming token is compared against this, so a token can never nominate the
    server that signs it.
    """
    pk = clerk_publishable_key()
    parts = pk.split("_", 2)
    if len(parts) != 3 or not parts[2]:
        return ""
    segment = parts[2]
    try:
        raw = base64.b64decode(segment + "=" * (-len(segment) % 4)).decode("utf-8", "replace")
    except (ValueError, binascii.Error):
        return ""
    host = raw.rstrip("$").strip()
    return "https://" + host if host else ""


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


EDGE_HEADER = "X-Assure-Edge"


def is_edge_request() -> bool:
    """True when the request was stamped by the public gate rather than arriving directly.

    The gate is the only thing the public hostnames reach, and it connects to this
    process from 127.0.0.1 — the same address an operator on the box uses. The
    header is how the two are told apart, and the gate overwrites any client value,
    so it cannot be spoofed from outside.
    """
    return (request.headers.get(EDGE_HEADER) or "").strip() == "1"


_ENV_FILE_CACHE: dict[str, tuple[float, dict[str, str]]] = {}


def _env_file_values(path: str) -> dict[str, str]:
    """Parse an env file, re-reading it whenever its mtime changes."""
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return {}
    cached = _ENV_FILE_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    values: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    _ENV_FILE_CACHE[path] = (mtime, values)
    return values


def _flag_value(name: str) -> str:
    """Read a flag from the env files, then the process environment.

    The files come first so a presenter can edit one line and have it take effect
    on the next request. `keys.load_keys()` copies those files into `os.environ` at
    boot, so preferring the file is what keeps this switch request-time instead of
    frozen at startup — a restart mid-demo is the failure the flag exists to avoid.
    Precedence across files matches load_keys(): .env, .env.local, then the
    ASSURE_ENV profile, later files winning.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    profile = (os.environ.get("ASSURE_ENV") or "").strip().lower()
    names = [".env", ".env.local"]
    if profile:
        names.append(".env." + profile)
    found: str | None = None
    for fname in names:
        value = _env_file_values(os.path.join(root, fname)).get(name)
        if value is not None:
            found = value
    if found is not None:
        return found
    return (os.environ.get(name) or "").strip()


def clerk_only_enabled() -> bool:
    """Clerk is the door unless the presenter flips `ASSURE_CLERK_ONLY=0`.

    Evaluated on every request and read from the box env file rather than from the
    environment copied at boot, so `0` restores the old behaviour with no restart:
    one line, no bounce.
    """
    return _flag_value("ASSURE_CLERK_ONLY").strip().lower() not in {"0", "false", "no", "off"}


def clerk_only_applies() -> bool:
    """True when this particular request must carry a session under clerk-only mode.

    A direct loopback call (an operator or a probe script on the box) keeps the
    old bypass: it is not reachable from the internet, and retiring it would break
    the probe tooling without closing anything. Traffic that came through the gate
    does not get that exemption.
    """
    if not clerk_only_enabled():
        return False
    return not (is_loopback_request() and not is_edge_request())


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


# Two roles, configured not modelled: admins are listed in the box env, every
# other signed-in user is an underwriter. There is no roles table and no per-user
# row to keep in sync — `ASSURE_ADMIN_USER_IDS` is the whole definition.
ROLE_ADMIN = "admin"
ROLE_UNDERWRITER = "underwriter"
ROLES = (ROLE_ADMIN, ROLE_UNDERWRITER)


def admin_user_ids() -> tuple[str, ...]:
    raw = os.environ.get("ASSURE_ADMIN_USER_IDS") or ""
    return tuple(part.strip() for part in raw.replace(";", ",").split(",") if part.strip())


def current_role() -> str:
    """`admin` when the session user is listed, `underwriter` for any other signed-in user, "" when nobody is."""
    user_id = current_user_id()
    if not user_id:
        return ""
    return ROLE_ADMIN if user_id in admin_user_ids() else ROLE_UNDERWRITER


def role_required(role: str):
    """Enforce `role` on a view for signed-in users.

    Like `middleware.ownership_enforced()`, this reads a Clerk identity: the
    shared-key operator is not a user, so the demo path stays as open as it is
    today. A signed-in user who is not in `ASSURE_ADMIN_USER_IDS` is denied.
    """

    def decorate(view):
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any):
            if not auth_required() or not current_user_id():
                return view(*args, **kwargs)
            if current_role() != role:
                return jsonify({"ok": False, "error": "Forbidden."}), 403
            return view(*args, **kwargs)

        return wrapped

    return decorate


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
            # Clerk's edge answers urllib's default `Python-urllib/3.x` with
            # Cloudflare 1010 (403), which surfaced as "session not valid" on
            # every sign-in. Any real product token passes.
            "User-Agent": CLERK_USER_AGENT,
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
    """Verify a Clerk session token and return the user behind it.

    Clerk retired `POST /sessions/{id}/verify` (HTTP 410, "endpoint is deprecated
    and pending removal"), so the token is verified the way Clerk now recommends:
    the RS256 signature is checked against the instance's published JWKS, the
    time claims are enforced, and the session is then confirmed live through the
    Backend API. Signature first, so a forged `sid` can never be pointed at
    somebody else's session.
    """
    header, payload, signed = _jwt_parts(token)
    _verify_signature(token, header, payload, signed)
    sid = payload.get("sid")
    if not isinstance(sid, str) or not sid:
        raise AuthError("missing session id")
    session = _clerk_json("GET", "/sessions/" + urllib.parse.quote(sid, safe=""))
    if str(session.get("status") or "") != "active":
        raise AuthError("session not active")
    user_id = session.get("user_id") or payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise AuthError("missing user id")
    if payload.get("sub") and payload["sub"] != user_id:
        raise AuthError("session does not match token")
    email = _user_email(user_id)
    return {"id": user_id, "email": email}


def _decode_segment(segment: str) -> dict[str, Any]:
    pad = "=" * (-len(segment) % 4)
    try:
        raw = base64.urlsafe_b64decode(segment + pad)
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthError("not a session token") from exc
    if not isinstance(data, dict):
        raise AuthError("not a session token")
    return data


def _jwt_parts(token: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Decode header/payload and return the signed segment verbatim."""
    parts = (token or "").split(".")
    if len(parts) != 3:
        raise AuthError("not a session token")
    return _decode_segment(parts[0]), _decode_segment(parts[1]), parts[0] + "." + parts[1]


def _b64_uint(segment: str) -> int:
    pad = "=" * (-len(segment) % 4)
    return int.from_bytes(base64.urlsafe_b64decode(segment + pad), "big")


def _instance_jwks(issuer: str) -> dict[str, Any]:
    """Fetch (and briefly cache) the instance's signing keys."""
    cached = _JWKS_CACHE.get(issuer)
    now = time.time()
    if cached and now - cached[0] < _JWKS_TTL_SECONDS:
        return cached[1]
    req = urllib.request.Request(
        issuer.rstrip("/") + "/.well-known/jwks.json",
        headers={"User-Agent": CLERK_USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise AuthError("could not reach Clerk") from exc
    parsed = json.loads(raw)
    keys = parsed.get("keys") if isinstance(parsed, dict) else None
    if not isinstance(keys, list) or not keys:
        raise AuthError("no signing keys published")
    by_kid = {str(k.get("kid") or ""): k for k in keys if isinstance(k, dict)}
    _JWKS_CACHE[issuer] = (now, by_kid)
    return by_kid


def _verify_signature(
    token: str, header: dict[str, Any], payload: dict[str, Any], signed: str
) -> None:
    if str(header.get("alg") or "") != "RS256":
        raise AuthError("unsupported token algorithm")
    issuer = str(payload.get("iss") or "")
    expected = instance_frontend_api()
    if not expected or issuer.rstrip("/") != expected:
        # The signing server is chosen by us, never by the token.
        raise AuthError("unexpected issuer")
    kid = str(header.get("kid") or "")
    keys = _instance_jwks(expected)
    jwk = keys.get(kid)
    if jwk is None:
        _JWKS_CACHE.pop(expected, None)  # rotated key: refetch once
        jwk = _instance_jwks(expected).get(kid)
    if not isinstance(jwk, dict):
        raise AuthError("unknown signing key")
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
    except ImportError as exc:  # pragma: no cover - declared in requirements.txt
        raise AuthError("cloud login is not set up") from exc
    try:
        public_key = rsa.RSAPublicNumbers(
            _b64_uint(str(jwk["e"])), _b64_uint(str(jwk["n"]))
        ).public_key()
        signature = base64.urlsafe_b64decode(token.split(".")[2] + "=" * (-len(token.split(".")[2]) % 4))
        public_key.verify(signature, signed.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    except AuthError:
        raise
    except Exception as exc:
        raise AuthError("session not valid") from exc
    now = int(time.time())
    exp = payload.get("exp")
    nbf = payload.get("nbf")
    if isinstance(exp, (int, float)) and now > int(exp) + _JWT_LEEWAY_SECONDS:
        raise AuthError("session token expired")
    if isinstance(nbf, (int, float)) and now < int(nbf) - _JWT_LEEWAY_SECONDS:
        raise AuthError("session token not yet valid")


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
    if path.startswith("/static/"):
        return None
    if path in PUBLIC_HTML or path in PUBLIC_API:
        return None
    if loopback_api_bypass() and not clerk_only_applies():
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
