#!/usr/bin/env python3
"""Local dev server for the shell prototype."""

from __future__ import annotations
import hashlib
import hmac
import json
import gzip
import threading
import time
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_ROOT = HERE
UPSTREAM_BASE = os.environ.get("UPSTREAM_BASE", "http://localhost:8899")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8990"))

FORWARD_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}

# ---------------------------------------------------------------------------
# The entry gate.
#
# This server is the only thing the public hostnames reach, and it used to
# serve the shell and proxy /api/* to anyone who asked. `SHELL_ACCESS_KEY` is
# now required: unauthenticated requests get 302 to /auth (shell and assets) or
# 401 (API). The key arrives three ways, all checked against the same secret:
#
#   X-Shell-Key: <key>      shell.js attaches this to every /api call
#   Authorization: Bearer   operator convenience (curl)
#   Cookie: assure_shell_key  set by a successful POST /auth, so <link>,
#                             <script> and streaming SSE carry it for free
#
# The same header/Bearer pair as `substrate.py:_authorize_worker_ingest`, so
# the two gates read alike. Unlike that one this gate fails closed: with no
# key configured the server refuses to start (see main()).
#
# Everything outside /api/* is served from disk except PROXIED_PAGES below:
# the Flask-rendered Clerk sign-in/up pages have no file in this static root,
# so a request for them goes upstream instead of 404ing.
# ---------------------------------------------------------------------------
ACCESS_KEY = (os.environ.get("SHELL_ACCESS_KEY") or "").strip()
if not ACCESS_KEY:
    # Fail loud, not fail-closed-invisibly: a blank key here once left the
    # gate running with an empty secret — nothing could authenticate (every
    # request 302/401'd) and staging was locked out with no error anywhere.
    sys.stderr.write(
        "SHELL_ACCESS_KEY is blank or missing; refusing to start the shell gate.\n"
        "Set it in the unit's EnvironmentFile (e.g. /etc/assure/shell-access.env).\n"
    )
    sys.exit(1)
COOKIE_NAME = "assure_shell_key"
AUTH_PATH = "/auth"
COOKIE_MAX_AGE = 2592000  # 30 days

# The Clerk sign-in/up pages are rendered by the Flask app (auth.html), not by
# this static root, so they are proxied upstream like /api/*. Everything else
# outside /api/* is still served from disk. These pages stay behind the entry
# gate: the gate key is the outer door, Clerk is the per-user identity inside it.
PROXIED_PAGES = frozenset({"/signin", "/signup", "/signout", "/parsing", "/connect"})

# Must match cloud_auth.EDGE_HEADER: it marks traffic that came through this gate.
EDGE_HEADER = "X-Assure-Edge"

# Shell documents. Extension-less paths are documents here; everything with an
# asset extension is inert bytes and stays served to any key holder.
_DOCUMENT_EXTENSIONS = frozenset({"", ".html"})

# `clerk_only` is read from the app rather than from this process's environment so
# the presenter flips one line in the box env and nothing is restarted — the gate
# picks the change up on its next request.


def _upstream_get(path: str, cookie: str = ""):
    headers = {"Accept": "application/json", "User-Agent": "assure-shell-gate/1.0"}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(UPSTREAM_BASE + path, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


CLERK_ONLY_CACHE_S = float(os.environ.get("CLERK_ONLY_CACHE_S", "5"))
_clerk_only_cache: tuple = ()


def clerk_only_mode() -> bool:
    """Whether the app is in clerk-only mode.

    Fetched per request, with no cache, so flipping the flag on the box changes this
    gate's behaviour on the very next request — the presenter must not need a
    restart of either unit.

    Fails closed. The config read is the only thing that reports whether Clerk is
    the door, so an app that cannot be reached — or a reply that does not carry the
    flag — is a state this gate cannot determine, and the conservative branch is
    Clerk-required. The two failure modes are not symmetrical: failing open serves
    the shell to a visitor with no session (the whole point of the flag), while
    failing closed asks for a session the presenter can supply, since the outer key
    gate is satisfied either way.
    """
    # Cached for CLERK_ONLY_CACHE_S (default 5 s): every document request made
    # an upstream round trip for this flag (audit 2026-09-24). A flip on the box
    # still shows within seconds, no restart.
    global _clerk_only_cache
    now = time.monotonic()
    if _clerk_only_cache and now - _clerk_only_cache[0] < CLERK_ONLY_CACHE_S:
        return _clerk_only_cache[1]
    try:
        config = _upstream_get("/api/auth/config")
    except Exception:
        return True  # unreachable: fail closed, and do not cache the failure
    if not isinstance(config, dict) or "clerk_only" not in config:
        # Unreadable is not off: same conservative branch as unreachable.
        return True
    value = bool(config["clerk_only"])
    _clerk_only_cache = (now, value)
    return value


def session_user_id(cookie: str) -> str:
    """The signed-in user id (Clerk or local) for this request's cookies, or ""."""
    if not cookie:
        return ""
    try:
        return str(_upstream_get("/api/auth/me", cookie=cookie).get("user_id") or "")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Local user management (2026-09-27). When the app reports
# `GET /api/auth/setup-status → {mode: "local", needs_owner, org_name}` the
# shared SHELL_ACCESS_KEY is no longer the door: the Flask session is. This
# server then serves three small pages itself — /setup (first run, the owner
# account, with the bootstrap token from the server's .env), /signin and
# /accept?token= (an invitation) — whose forms POST to the proxied API; the
# Set-Cookie the app answers with flows back through _proxy unchanged (every
# upstream response header except the hop-by-hop ones is copied), so the
# session cookie lands on this origin. Documents are served only to a request
# whose cookie /api/auth/me knows; assets stay inert bytes. Modes "off" and
# "clerk" (and an app that cannot be reached, or one without the route) keep
# the key gate exactly as it was.
# ---------------------------------------------------------------------------
LOCAL_PAGES = frozenset({"/setup", "/signin", "/accept"})
AUTH_STATUS_CACHE_S = float(os.environ.get("AUTH_STATUS_CACHE_S", "5"))
_auth_status_cache: tuple = ()


def auth_status() -> dict:
    """The app's `setup-status`, cached AUTH_STATUS_CACHE_S seconds. An app
    that cannot be reached or does not have the route reads as mode "off"
    (the key gate stays), never as "local"."""
    global _auth_status_cache
    now = time.monotonic()
    if _auth_status_cache and now - _auth_status_cache[0] < AUTH_STATUS_CACHE_S:
        return _auth_status_cache[1]
    try:
        status = _upstream_get("/api/auth/setup-status")
    except Exception:
        return {"mode": "off", "needs_owner": False, "org_name": ""}
    if not isinstance(status, dict) or str(status.get("mode") or "") not in ("local", "clerk", "off"):
        return {"mode": "off", "needs_owner": False, "org_name": ""}
    value = {"mode": str(status.get("mode")), "needs_owner": bool(status.get("needs_owner")), "org_name": str(status.get("org_name") or "")}
    _auth_status_cache = (now, value)
    return value


def local_auth_mode() -> bool:
    return auth_status().get("mode") == "local"


def render_local_page(kind: str, *, status: dict | None = None, next_path: str = "/", token: str = "", error: str = "") -> str:
    """The /setup, /signin and /accept pages — same quiet page as the key gate,
    one form each. Values are HTML-escaped; the form posts JSON to the API
    from a small script so the answer's error sentence can be shown inline."""
    import html as _html

    st = status or {}
    org = _html.escape(st.get("org_name") or "")
    nxt = next_path if next_path.startswith("/") and not next_path.startswith("//") else "/"
    if kind == "setup":
        title, lede = "Set up Assure", (
            "First run: create the owner account for this installation. "
            "The bootstrap token is the one the server's .env holds; scripts/gen-env.sh prints it once when it writes that file."
        )
        fields = (
            '<label for="org_name">Organisation</label><input id="org_name" name="org_name" type="text" autocomplete="organization" value="%s" required>'
            '<label for="email">Owner e-mail</label><input id="email" name="email" type="email" autocomplete="username" required>'
            '<label for="display_name">Display name</label><input id="display_name" name="display_name" type="text" autocomplete="name" required>'
            '<label for="password">Password</label><input id="password" name="password" type="password" autocomplete="new-password" minlength="12" required>'
            '<label for="password2">Password, again</label><input id="password2" name="password2" type="password" autocomplete="new-password" minlength="12" required>'
            '<label for="bootstrap_token">Bootstrap token</label><input id="bootstrap_token" name="bootstrap_token" type="password" autocomplete="off" required>'
        ) % org
        action, button = "/api/auth/setup", "Create owner account"
    elif kind == "accept":
        title, lede = "Join Assure", "You were invited. Choose a display name and a password to finish."
        fields = (
            '<input id="token" name="token" type="hidden" value="%s">'
            '<label for="display_name">Display name</label><input id="display_name" name="display_name" type="text" autocomplete="name" required autofocus>'
            '<label for="password">Password</label><input id="password" name="password" type="password" autocomplete="new-password" minlength="12" required>'
            '<label for="password2">Password, again</label><input id="password2" name="password2" type="password" autocomplete="new-password" minlength="12" required>'
        ) % _html.escape(token)
        action, button = "/api/auth/accept-invitation", "Join"
    else:
        title, lede = ("Sign in to %s" % org) if org else "Sign in", "Use the e-mail and password of your Assure account."
        fields = (
            '<label for="email">E-mail</label><input id="email" name="email" type="email" autocomplete="username" required autofocus>'
            '<label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required>'
        )
        action, button = "/api/auth/login", "Sign in"
    return (_LOCAL_PAGE
            .replace("__TITLE__", _html.escape(title))
            .replace("__LEDE__", _html.escape(lede))
            .replace("__ERROR__", _html.escape(error))
            .replace("__FIELDS__", fields)
            .replace("__ACTION__", action)
            .replace("__BUTTON__", button)
            .replace("__KIND__", kind)
            .replace("__NEXT__", _html.escape(nxt)))


_LOCAL_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Assure AI — __TITLE__</title>
  <style>
    html, body { height: 100%; margin: 0; }
    body {
      display: flex; align-items: center; justify-content: center;
      background: #111; color: #e8e8e8;
      font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif;
      padding: 24px 16px; box-sizing: border-box;
    }
    main { width: 100%; max-width: 340px; }
    h1 { font-size: 15px; font-weight: 600; letter-spacing: .01em; margin: 0 0 4px; }
    p { margin: 0 0 20px; color: #8a8a8a; font-size: 12px; }
    label { display: block; font-size: 11px; letter-spacing: .08em;
            text-transform: uppercase; color: #8a8a8a; margin-bottom: 6px; }
    input, button { width: 100%; box-sizing: border-box; border-radius: 0; }
    input {
      background: #1a1a1a; border: 1px solid #333; color: #e8e8e8;
      padding: 9px 10px; font: inherit; margin-bottom: 10px;
    }
    input:focus { outline: none; border-color: #666; }
    button {
      background: #e8e8e8; border: 0; color: #111; padding: 10px;
      font: inherit; font-weight: 600; cursor: pointer;
    }
    button[disabled] { opacity: .6; cursor: default; }
    .err { color: #e07a7a; font-size: 12px; margin: 0 0 10px; min-height: 0; }
    .foot { margin: 16px 0 0; font-size: 12px; color: #8a8a8a; }
    .foot a { color: #c8c8c8; }
  </style>
</head>
<body>
  <main>
    <h1>__TITLE__</h1>
    <p>__LEDE__</p>
    <p class="err" id="err" role="alert">__ERROR__</p>
    <form method="POST" action="__ACTION__" id="local-auth" data-kind="__KIND__" data-next="__NEXT__">
      __FIELDS__
      <button type="submit" id="submit">__BUTTON__</button>
    </form>
  </main>
  <script>
    // The form posts JSON to the API through this same origin; the app's
    // Set-Cookie comes back through the proxy and the browser keeps it. The
    // server's error sentence is shown as written (401 wrong password, 423
    // locked, 403 disabled or bad token, 400 the field it names).
    (function () {
      var form = document.getElementById("local-auth");
      var err = document.getElementById("err");
      var submit = document.getElementById("submit");
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        err.textContent = "";
        var body = {};
        Array.prototype.forEach.call(form.elements, function (el) { if (el.name) body[el.name] = el.value; });
        if ("password2" in body) {
          if (body.password !== body.password2) { err.textContent = "The two passwords differ."; return; }
          delete body.password2;
        }
        submit.disabled = true;
        fetch(form.getAttribute("action"), { method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json", "Accept": "application/json" }, body: JSON.stringify(body) })
          .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { status: r.status, j: j || {} }; }); })
          .then(function (res) {
            if (res.status >= 200 && res.status < 300 && res.j.ok !== false) { window.location.replace(form.getAttribute("data-next") || "/"); return; }
            var kind = form.getAttribute("data-kind");
            err.textContent = res.j.error || (res.status === 401 ? "That e-mail or password was not accepted."
              : res.status === 423 ? (res.j.locked_until ? "This account is locked until " + res.j.locked_until + "." : "This account is locked for now. Try again later.")
              : res.status === 404 && kind === "accept" ? "This invitation is unknown or has expired."
              : res.status === 403 ? "Not allowed." : "That did not go through (HTTP " + res.status + ").");
            submit.disabled = false;
          })
          .catch(function () { err.textContent = "The server could not be reached. Check your connection and try again."; submit.disabled = false; });
      });
    })();
  </script>
</body>
</html>
"""


def is_document_path(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in _DOCUMENT_EXTENSIONS

_AUTH_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Assure AI — Restricted</title>
  <style>
    html, body { height: 100%; margin: 0; }
    body {
      display: flex; align-items: center; justify-content: center;
      background: #111; color: #e8e8e8;
      font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif;
    }
    main { width: 320px; }
    h1 { font-size: 15px; font-weight: 600; letter-spacing: .01em; margin: 0 0 4px; }
    p { margin: 0 0 20px; color: #8a8a8a; font-size: 12px; }
    label { display: block; font-size: 11px; letter-spacing: .08em;
            text-transform: uppercase; color: #8a8a8a; margin-bottom: 6px; }
    input, button { width: 100%%; box-sizing: border-box; border-radius: 0; }
    input {
      background: #1a1a1a; border: 1px solid #333; color: #e8e8e8;
      padding: 9px 10px; font: inherit; margin-bottom: 10px;
    }
    input:focus { outline: none; border-color: #666; }
    button {
      background: #e8e8e8; border: 0; color: #111; padding: 10px;
      font: inherit; font-weight: 600; cursor: pointer;
    }
    .err { color: #e07a7a; font-size: 12px; margin: 0 0 10px; min-height: 0; }
  </style>
</head>
<body>
  <main>
    <h1>Assure AI</h1>
    <p>This build is not public. Enter the access key to continue.</p>
    <p class="err">__ERROR__</p>
    <form method="POST" action="/auth" id="gate">
      <label for="key">Access key</label>
      <input id="key" name="key" type="password" autocomplete="current-password" autofocus>
      <button type="submit">Continue</button>
    </form>
  </main>
  <script>
    // The gate is the only place the key is typed. Keep it in localStorage so
    // shell.js can put it on every API call; the POST sets the cookie.
    document.getElementById("gate").addEventListener("submit", function () {
      try { window.localStorage.setItem("assure_shell_key", document.getElementById("key").value); } catch (_) {}
    });
  </script>
</body>
</html>
"""


def _key_matches(presented):
    """Constant-time compare against the configured key."""
    if not presented or not ACCESS_KEY:
        return False
    return hmac.compare_digest(
        hashlib.sha256(presented.encode("utf-8")).digest(),
        hashlib.sha256(ACCESS_KEY.encode("utf-8")).digest(),
    )


_STATIC_CACHE: dict = {}
_STATIC_CACHE_LOCK = threading.Lock()
_GZIP_TYPES = (".html", ".css", ".js", ".json", ".svg")


def _static_payload(fs_path):
    """(body, etag, gzipped-or-None) for a static file, cached per (mtime, size).

    The cache key is the file's mtime and size, so an edited file is served
    fresh on the next request with a new ETag; nothing is cached across restarts.
    """
    st = os.stat(fs_path)
    key = (fs_path, st.st_mtime_ns, st.st_size)
    with _STATIC_CACHE_LOCK:
        hit = _STATIC_CACHE.get(fs_path)
        if hit and hit[0] == key:
            return hit[1], hit[2], hit[3]
    with open(fs_path, "rb") as fh:
        body = fh.read()
    etag = '"%s"' % hashlib.sha1(body).hexdigest()[:20]
    gz = None
    if os.path.splitext(fs_path)[1].lower() in _GZIP_TYPES and len(body) > 1024:
        gz = gzip.compress(body, compresslevel=6)
    with _STATIC_CACHE_LOCK:
        _STATIC_CACHE[fs_path] = (key, body, etag, gz)
    return body, etag, gz


class Handler(BaseHTTPRequestHandler):
    server_version = "AssureShellProxy/1.0"

    def log_message(self, fmt, *args):
        return

    # ------------------------------------------------------------------
    # The entry gate
    # ------------------------------------------------------------------
    def _presented_key(self):
        header = (self.headers.get("X-Shell-Key") or "").strip()
        if header:
            return header
        auth = (self.headers.get("Authorization") or "").strip()
        if auth.startswith("Bearer "):
            return auth[7:].strip()
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE_NAME:
                return value.strip()
        return ""

    def _authorized(self):
        return _key_matches(self._presented_key())

    def _deny(self):
        """302 for the shell, 401 for the API — a request never reaches data."""
        if self.path.startswith("/api/"):
            body = b'{"ok":false,"error":"unauthorized"}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("WWW-Authenticate", 'Bearer realm="assure-shell"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return
        self.send_response(302)
        self.send_header("Location", AUTH_PATH)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _serve_auth_page(self, error=False):
        body = _AUTH_PAGE.replace("__ERROR__", "That key was not accepted." if error else "").encode(
            "utf-8"
        )
        self.send_response(401 if error else 200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _handle_auth(self, method):
        if method in ("GET", "HEAD"):
            self._serve_auth_page()
            return
        if method != "POST":
            self.send_error(405)
            return
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length).decode("utf-8", "replace") if length > 0 else ""
        presented = (urllib.parse.parse_qs(raw).get("key") or [""])[0].strip()
        if not _key_matches(presented):
            self._serve_auth_page(error=True)
            return
        self.send_response(302)
        self.send_header("Location", "/")
        # `Secure` only when the visitor actually came over HTTPS (directly or via
        # a proxy/tunnel that says so). Over plain HTTP — a fresh EC2 on port 80
        # before TLS is in front — a Secure cookie is dropped by the browser, so
        # every correct key looped straight back to /auth (2026-09-24).
        forwarded = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        https = forwarded == "https" or os.environ.get("SHELL_COOKIE_SECURE", "").lower() in ("1", "true", "yes")
        self.send_header(
            "Set-Cookie",
            "%s=%s; Path=/; Max-Age=%d; HttpOnly;%s SameSite=Lax"
            % (COOKIE_NAME, urllib.parse.quote(presented, safe=""), COOKIE_MAX_AGE, " Secure;" if https else ""),
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _redirect_to_signin(self, target_path: str = "/signin"):
        path = self.path.split("?", 1)[0]
        target = target_path
        if path not in ("/", "/index.html"):
            nxt = self.path if self.path.startswith("/") else "/"
            target = target_path + "?" + urllib.parse.urlencode({"next": nxt})
        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _serve_local_page(self, method):
        """/setup, /signin, /accept in local mode (see LOCAL_PAGES)."""
        if method not in ("GET", "HEAD"):
            self.send_error(405)
            return
        path, _, query = self.path.partition("?")
        qs = urllib.parse.parse_qs(query)
        status = auth_status()
        kind = path.lstrip("/")
        if kind == "setup" and not status.get("needs_owner"):
            # The owner exists: there is nothing to set up. Sign in instead.
            self.send_response(302)
            self.send_header("Location", "/signin")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if kind == "signin" and status.get("needs_owner"):
            self.send_response(302)
            self.send_header("Location", "/setup")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        body = render_local_page(kind, status=status, next_path=(qs.get("next") or ["/"])[0], token=(qs.get("token") or [""])[0]).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _serve_static(self):
        path = self.path.split("?", 1)[0] or "/index.html"
        if path == "/":
            path = "/index.html"
        fs_path = os.path.abspath(os.path.join(STATIC_ROOT, path.lstrip("/")))
        if not fs_path.startswith(os.path.abspath(STATIC_ROOT)):
            self.send_error(403, "Forbidden")
            return
        if not os.path.isfile(fs_path):
            self.send_error(404, "Not Found")
            return
        ext = os.path.splitext(fs_path)[1].lower()
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(ext, "application/octet-stream")
        body, etag, gz = _static_payload(fs_path)
        # Every asset used to be `Cache-Control: no-store`: shell.js (315 KB) and
        # shell.css (93 KB) were re-downloaded, uncompressed, on every page load.
        # Now: strong ETag → 304 on revalidation, gzip when accepted, and a short
        # max-age so a deploy shows up within a minute (dev-server serves the
        # working tree, so the ETag changes the moment a file does).
        if self.headers.get("If-None-Match", "").strip() == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "public, max-age=60, must-revalidate")
            self.end_headers()
            return
        accept_gzip = "gzip" in (self.headers.get("Accept-Encoding") or "").lower()
        payload = gz if (gz is not None and accept_gzip) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("ETag", etag)
        self.send_header("Vary", "Accept-Encoding")
        if payload is gz:
            self.send_header("Content-Encoding", "gzip")
        self.send_header(
            "Cache-Control",
            "no-cache" if ext == ".html" else "public, max-age=60, must-revalidate",
        )
        self.end_headers()
        self.wfile.write(payload)

    def _proxy(self, method):
        if method not in FORWARD_METHODS:
            self.send_error(405)
            return
        url = UPSTREAM_BASE + self.path
        cl = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(cl) if cl > 0 else None
        hdrs = {}
        for k in (
            "Content-Type",
            "Accept",
            "Authorization",
            # The Flask session lives in a cookie; without this the upstream app
            # sees an anonymous client and every signed-in call looks signed out.
            "Cookie",
            "Origin",
            "Cache-Control",
            "X-Requested-With",
        ):
            v = self.headers.get(k)
            if v:
                hdrs[k] = v
        # The visitor's address and scheme, for the app's audit rows (Flask
        # honours PROXY_FIX_HOPS=1): the client IP is appended to any chain a
        # proxy in front already wrote, never replaced.
        client_ip = (self.client_address[0] if self.client_address else "") or ""
        xff = (self.headers.get("X-Forwarded-For") or "").strip()
        hdrs["X-Forwarded-For"] = (xff + ", " + client_ip) if (xff and client_ip) else (xff or client_ip)
        proto = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        hdrs["X-Forwarded-Proto"] = proto or ("https" if os.environ.get("SHELL_COOKIE_SECURE", "").lower() in ("1", "true", "yes") else "http")
        # Tell the app this request came through the gate. Assigned last and
        # unconditionally so a client can never supply its own value.
        hdrs[EDGE_HEADER] = "1"
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=None) as resp:
                # The owner was just created (or someone signed in / out): the
                # cached setup-status is stale the moment the app answers 2xx.
                if self.path.split("?", 1)[0] in ("/api/auth/setup", "/api/auth/login", "/api/auth/logout") and 200 <= resp.status < 300:
                    global _auth_status_cache
                    _auth_status_cache = ()
                self.send_response(resp.status)
                seen = set()
                for k, v in resp.getheaders():
                    kl = k.lower()
                    if kl in (
                        "connection",
                        "transfer-encoding",
                        "content-encoding",
                        "keep-alive",
                        "upgrade",
                    ):
                        continue
                    if kl in seen:
                        continue
                    seen.add(kl)
                    self.send_header(k, v)
                self.end_headers()
                while True:
                    # read1 returns as soon as any bytes land; read() would block
                    # until 4 KB accumulate, so SSE frames never reached the edge.
                    chunk = resp.read1(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            for k, v in e.headers.items() if hasattr(e.headers, "items") else []:
                if k.lower() in ("connection", "transfer-encoding", "content-encoding"):
                    continue
                self.send_header(k, v)
            self.end_headers()
            try:
                b = e.read()
            except Exception:
                b = b""
            if b:
                self.wfile.write(b)
        except urllib.error.URLError as e:
            msg = ("Upstream unavailable: %s" % (e.reason,)).encode()
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
        except BrokenPipeError:
            pass

    def _route(self, method):
        path_only = self.path.split("?", 1)[0]
        if local_auth_mode():
            # Local accounts: the session is the door, not the shared key.
            if path_only == AUTH_PATH:
                self.send_response(302)
                self.send_header("Location", "/signin")
                self.end_headers()
                return
            if path_only in LOCAL_PAGES:
                self._serve_local_page(method)
                return
            if self.path.startswith("/api/"):
                self._proxy(method)
                return
            if path_only in PROXIED_PAGES or self.path.startswith("/parsing/"):
                if method not in ("GET", "HEAD"):
                    self.send_error(405)
                    return
                if not session_user_id(self.headers.get("Cookie") or ""):
                    self._redirect_to_signin("/setup" if auth_status().get("needs_owner") else "/signin")
                    return
                self._proxy(method)
                return
            if method != "GET":
                self.send_error(405)
                return
            if is_document_path(path_only) and not session_user_id(self.headers.get("Cookie") or ""):
                self._redirect_to_signin("/setup" if auth_status().get("needs_owner") else "/signin")
                return
            self._serve_static()
            return
        if path_only == AUTH_PATH:
            self._handle_auth(method)
            return
        if not self._authorized():
            self._deny()
            return
        # Legacy: /app* redirects to the shell root.
        if method == "GET" and (
            self.path == "/app" or self.path.startswith("/app?") or self.path.startswith("/app/")
        ):
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return
        if self.path.startswith("/api/"):
            self._proxy(method)
        elif self.path.split("?", 1)[0] in PROXIED_PAGES or self.path.startswith("/parsing/"):
            # /parsing/<report_id> is the Parsure document detail page (Flask),
            # added 2026-09-26; the set above lists exact paths only.
            if method not in ("GET", "HEAD"):
                self.send_error(405)
                return
            self._proxy(method)
        else:
            if method != "GET":
                self.send_error(405)
                return
            # The front door needs a Clerk session, not just the shared key. The
            # session lives with the app, so it is the app we ask. Assets are not
            # documents and are not gated: they are inert bytes and the key still
            # covers them.
            if clerk_only_mode() and is_document_path(self.path.split("?", 1)[0]):
                if not session_user_id(self.headers.get("Cookie") or ""):
                    self._redirect_to_signin()
                    return
            self._serve_static()

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def do_PATCH(self):
        self._route("PATCH")

    def do_HEAD(self):
        self._route("HEAD")

    def do_OPTIONS(self):
        self._route("OPTIONS")


def main():
    # Allow running without an access key for staging (no fail-closed check)
    # Staging doesn't need an entrance key; production will have Clerk
    if not ACCESS_KEY:
        print(
            "Dev server on http://{}:{} (no access key, staging mode - public access)".format(HOST, PORT),
            flush=True,
        )
    else:
        print(
            "Dev server on http://{}:{} (proxying /api/* and {} to {})".format(
                HOST, PORT, ",".join(sorted(PROXIED_PAGES)), UPSTREAM_BASE
            ),
            flush=True,
        )
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        print(
            "Dev server on http://{}:{} (proxying /api/* and {} to {})".format(
                HOST, PORT, ",".join(sorted(PROXIED_PAGES)), UPSTREAM_BASE
            ),
            flush=True,
        )
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
