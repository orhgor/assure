#!/usr/bin/env python3
"""Local dev server for the shell prototype."""

from __future__ import annotations
import hashlib
import hmac
import json
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
    try:
        config = _upstream_get("/api/auth/config")
    except Exception:
        return True
    if not isinstance(config, dict) or "clerk_only" not in config:
        # Unreadable is not off: same conservative branch as unreachable.
        return True
    return bool(config["clerk_only"])


def session_user_id(cookie: str) -> str:
    """The signed-in Clerk user id for this request's cookies, or ""."""
    if not cookie:
        return ""
    try:
        return str(_upstream_get("/api/auth/me", cookie=cookie).get("user_id") or "")
    except Exception:
        return ""


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

    def _redirect_to_signin(self):
        path = self.path.split("?", 1)[0]
        target = "/signin"
        if path not in ("/", "/index.html"):
            nxt = self.path if self.path.startswith("/") else "/"
            target = "/signin?" + urllib.parse.urlencode({"next": nxt})
        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

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
        with open(fs_path, "rb") as fh:
            body = fh.read()
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
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

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
        # Tell the app this request came through the gate. Assigned last and
        # unconditionally so a client can never supply its own value.
        hdrs[EDGE_HEADER] = "1"
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=None) as resp:
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
        if self.path.split("?", 1)[0] == AUTH_PATH:
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
        elif self.path.split("?", 1)[0] in PROXIED_PAGES:
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
