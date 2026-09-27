"""The shell gate (prototype/dev-server.py) in local-accounts mode (2026-09-27).

The gate cannot be driven end to end here (it proxies a live app), so this
checks the pieces that decide the door: the three local pages it renders, the
mode it reads from ``/api/auth/setup-status`` (unreachable / unknown → "off",
never "local"), the routing rules as written, and that the proxy still copies
every upstream header back so the app's Set-Cookie lands on this origin.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "prototype" / "dev-server.py"


@pytest.fixture(scope="module")
def gate(monkeypatch_module=None):
    import os

    os.environ.setdefault("SHELL_ACCESS_KEY", "test-key")
    spec = importlib.util.spec_from_file_location("assure_dev_server", GATE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_local_pages_have_the_forms_the_contract_needs(gate):
    setup = gate.render_local_page("setup", status={"needs_owner": True, "org_name": "Acme <Co>"}, next_path="/")
    for name in ("org_name", "email", "display_name", "password", "password2", "bootstrap_token"):
        assert re.search(r'<input[^>]*name="%s"' % name, setup), name
    assert 'action="/api/auth/setup"' in setup and "Acme &lt;Co&gt;" in setup
    # The one quiet sentence about where the token comes from.
    assert ".env" in setup and "gen-env.sh" in setup

    signin = gate.render_local_page("signin", status={"needs_owner": False, "org_name": "Acme"}, next_path="/parsing?project_id=p1")
    assert 'action="/api/auth/login"' in signin and 'name="email"' in signin and 'name="password"' in signin
    assert 'data-next="/parsing?project_id=p1"' in signin and "Sign in to Acme" in signin
    # An open redirect is not a next.
    assert 'data-next="/"' in gate.render_local_page("signin", next_path="//evil.example")
    assert 'data-next="/"' in gate.render_local_page("signin", next_path="https://evil.example")

    accept = gate.render_local_page("accept", token='tok"<x>', next_path="/")
    assert 'action="/api/auth/accept-invitation"' in accept
    assert 'name="token" type="hidden" value="tok&quot;&lt;x&gt;"' in accept
    assert 'name="display_name"' in accept and 'name="password2"' in accept
    # The forms post JSON with the session cookie and show the server's sentence.
    for page in (setup, signin, accept):
        assert 'credentials: "same-origin"' in page and "res.j.error" in page
        assert "423" in page  # a locked account is named, not a generic failure


def test_auth_status_reads_off_when_the_app_cannot_be_reached_or_lacks_the_route(gate, monkeypatch):
    gate._auth_status_cache = ()

    def boom(path, cookie=""):
        raise OSError("connection refused")

    monkeypatch.setattr(gate, "_upstream_get", boom)
    assert gate.auth_status()["mode"] == "off" and gate.local_auth_mode() is False
    monkeypatch.setattr(gate, "_upstream_get", lambda path, cookie="": {"error": "not found"})
    assert gate.auth_status()["mode"] == "off"
    monkeypatch.setattr(gate, "_upstream_get", lambda path, cookie="": {"ok": True, "mode": "local", "needs_owner": True, "org_name": "Acme"})
    gate._auth_status_cache = ()
    st = gate.auth_status()
    assert st == {"mode": "local", "needs_owner": True, "org_name": "Acme"} and gate.local_auth_mode() is True
    gate._auth_status_cache = ()


def test_routing_rules_as_written(gate):
    src = GATE.read_text(encoding="utf-8")
    assert gate.LOCAL_PAGES == frozenset({"/setup", "/signin", "/accept"})
    # Local mode: the key gate steps aside, /auth goes to /signin, documents need a session.
    local_block = src.split("if local_auth_mode():", 1)[1].split("if path_only == AUTH_PATH:\n            self._handle_auth(method)", 1)[0]
    assert "self._serve_local_page(method)" in local_block
    assert "if self.path.startswith(\"/api/\"):\n                self._proxy(method)" in local_block
    assert 'self._redirect_to_signin("/setup" if auth_status().get("needs_owner") else "/signin")' in local_block
    assert "is_document_path(path_only) and not session_user_id" in local_block
    # Off / clerk: the key gate exactly as before.
    assert "if not self._authorized():\n            self._deny()" in src
    # The proxy forwards the cookie up and every upstream header (Set-Cookie included) back.
    proxy = src.split("def _proxy(self, method):", 1)[1].split("def _route", 1)[0]
    assert '"Cookie",' in proxy and "for k, v in resp.getheaders():" in proxy and '"set-cookie"' not in proxy.lower().replace("set-cookie", "set-cookie")  # never filtered
    assert "Secure" in src.split("def _handle_auth", 1)[1].split("def _redirect_to_signin", 1)[0]  # the key cookie's Secure logic is untouched
    # The visitor's IP and scheme reach the app for its audit rows (appended, never replaced).
    assert 'hdrs["X-Forwarded-For"] = (xff + ", " + client_ip)' in proxy and 'hdrs["X-Forwarded-Proto"]' in proxy
    # The pages name the locked-until time and an unknown invitation.
    assert "locked_until" in gate._LOCAL_PAGE and "This invitation is unknown or has expired." in gate._LOCAL_PAGE
