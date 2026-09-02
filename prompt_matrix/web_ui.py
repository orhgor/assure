"""Sign-in for the PEM Flask UI.

On 127.0.0.1, sign-in is off unless PEM_HTTP_PASS (or --http-pass) is set to
something other than the old default. Binding to 0.0.0.0 requires a password
and refuses to start with the default.
"""

from __future__ import annotations

import os

from flask import Flask, request
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import check_password_hash, generate_password_hash

auth = HTTPBasicAuth()

_users: dict[str, str] = {}

PUBLIC_PATHS = frozenset(
    {"/api/health", "/api/status", "/api/webhook/stripe", "/api/webhooks/stripe", "/api/waitlist"}
)


def refresh_http_users(*, user: str | None = None, password: str | None = None) -> None:
    if user:
        os.environ["PEM_HTTP_USER"] = user
    if password:
        os.environ["PEM_HTTP_PASS"] = password
    name = os.getenv("PEM_HTTP_USER", "admin")
    secret = os.getenv("PEM_HTTP_PASS", "changeme")
    global _users
    _users = {name: generate_password_hash(secret)}


@auth.verify_password
def verify_password(username: str, password: str) -> str | None:
    hashed = _users.get(username)
    if hashed and check_password_hash(hashed, password):
        return username
    return None


def protect_app(app: Flask) -> None:
    """Require Basic Auth on every route except GET /api/health."""
    refresh_http_users()

    @app.before_request
    def _require_login():
        if request.path in PUBLIC_PATHS:
            return None
        return auth.login_required(lambda: None)()
