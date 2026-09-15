"""Shared pytest fixtures for Assure workbench integration tests."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest
from werkzeug.serving import make_server

# CSS @media (max-width: 1024px) shows a mobile hint; the workbench stays usable.
# Playwright's default 1280×720 is technically wide enough; pin desktop anyway.
DESKTOP_VIEWPORT = {"width": 1440, "height": 900}
_SYSTEM_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def live_assure_server():
    """Start a real Flask app on a random port for Playwright.

    Clerk / production auth is off (``create_app(require_auth=False)``). Tests
    must not assume a signed-in user or live model keys.
    """
    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "playwright.sqlite"

    def _getter():
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    class _StatVfs:
        f_frsize = 4096
        f_bavail = 2_000_000

    patches = [
        patch("prompt_matrix.history.DB_PATH", db_path),
        patch("prompt_matrix.history._resolve_db_path", return_value=db_path),
        patch("prompt_matrix.db.pool._resolve_db_path", return_value=db_path),
        patch("prompt_matrix.lib.logger.DB_PATH", db_path),
        patch("prompt_matrix.lib.logger.resolve_db_path", return_value=str(db_path)),
        patch("prompt_matrix.history.get_db", side_effect=_getter),
        patch("prompt_matrix.db.connection.get_db", side_effect=_getter),
        patch("prompt_matrix.db.jdf_repository.get_db", side_effect=_getter),
        patch("prompt_matrix.db.feedback_repository.get_db", side_effect=_getter),
        patch("prompt_matrix.routers.health.os.statvfs", return_value=_StatVfs()),
        # Blank Clerk keys so auth is off for the Playwright server only.
        # Do not set ASSURE_REQUIRE_LOGIN=false here — that leaks into unrelated tests.
        patch.dict(
            os.environ,
            {
                "CLERK_PUBLISHABLE_KEY": "",
                "CLERK_SECRET_KEY": "",
                "SQLITE_USE_POOL": "0",
            },
            clear=False,
        ),
    ]
    for item in patches:
        item.start()

    from prompt_matrix.db.pool import reset_engine_for_tests

    reset_engine_for_tests()

    conn = _getter()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.web import create_app

    init_db(conn)
    app = create_app(require_auth=False)
    port = _free_port()
    server = make_server("127.0.0.1", port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="assure-playwright")
    thread.start()
    base_url = f"http://127.0.0.1:{port}"

    yield base_url

    server.shutdown()
    conn.close()
    for item in patches:
        item.stop()
    tmp.cleanup()


@pytest.fixture(scope="session")
def base_url(live_assure_server):
    """pytest-playwright uses this for relative navigations (page.goto('/app'))."""
    return live_assure_server


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Keep the workbench visible: viewport must be wider than 1024px."""
    return {
        **browser_context_args,
        "viewport": DESKTOP_VIEWPORT,
    }


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Prefer system Chrome when the Playwright browser cache cannot be filled.

    ``playwright install chromium`` needs ~95MiB zip + extract; this machine's
    Data volume was at ENOSPC. Channel ``chrome`` uses
    ``/Applications/Google Chrome.app`` and needs no extra download.
    Linux CI without that app keeps the bundled Chromium.
    """
    if _SYSTEM_CHROME.is_file():
        return {**browser_type_launch_args, "channel": "chrome"}
    return browser_type_launch_args


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "playwright: browser-driven Assure workbench simulation (requires pytest-playwright)",
    )


# ---- Prototype shell cross-layer honesty suite (appended; additive) ----
# Targets the prototype shell (http://localhost:8990/index.html) which proxies
# /api/* to the Flask app (http://localhost:8899). These do NOT reuse the
# workbench `live_assure_server` / `base_url` fixtures above.

SHELL_BASE = os.environ.get("SHELL_BASE_URL", "http://localhost:8990")
API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8899")


def _servers_up() -> tuple[bool, bool]:
    def _ok(url: str) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=4) as r:
                return r.status == 200
        except Exception:
            return False

    return _ok(SHELL_BASE + "/index.html"), _ok(API_BASE + "/health")


def _shell_db_path():
    env = os.environ.get("DATABASE_PATH")
    if env:
        return Path(env)
    cand = Path("prompt_matrix/history.sqlite")
    if cand.exists():
        return cand
    try:
        from prompt_matrix.history import _resolve_db_path
    except Exception:
        return Path("prompt_matrix/history.sqlite")
    return _resolve_db_path()


def _conn():
    return sqlite3.connect(str(_shell_db_path()), timeout=30.0, isolation_level=None)


def parse_verified(raw) -> dict | None:
    """Pull the last `verified` SSE payload out of a raw /draft/stream body."""
    text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
    verified = None
    for event in re.split(r"\n(?=event:)", text):
        e = next((ln[6:].strip() for ln in event.splitlines() if ln.startswith("event:")), None)
        if e != "verified":
            continue
        data_lines = [ln[5:].strip() for ln in event.splitlines() if ln.startswith("data:")]
        if not data_lines:
            continue
        try:
            obj = json.loads("\n".join(data_lines))
        except Exception:
            continue
        if isinstance(obj, dict):
            verified = obj
    return verified


def parse_compiled(raw) -> dict | None:
    text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
    compiled = None
    for event in re.split(r"\n(?=event:)", text):
        e = next((ln[6:].strip() for ln in event.splitlines() if ln.startswith("event:")), None)
        if e != "compiled":
            continue
        data_lines = [ln[5:].strip() for ln in event.splitlines() if ln.startswith("data:")]
        if not data_lines:
            continue
        try:
            obj = json.loads("\n".join(data_lines))
        except Exception:
            continue
        if isinstance(obj, dict):
            compiled = obj
    return compiled


@pytest.fixture(scope="session")
def shell_servers_up():
    ok_shell, ok_api = _servers_up()
    if not (ok_shell and ok_api):
        pytest.skip(
            f"e2e needs both servers: shell={SHELL_BASE}/index.html up={ok_shell} "
            f"api={API_BASE}/health up={ok_api}"
        )
    return {"shell": SHELL_BASE, "api": API_BASE}


@pytest.fixture(scope="session")
def browser_page(shell_servers_up):
    """Session-scoped page for the prototype shell (independent browser)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        launch: dict = {}
        if _SYSTEM_CHROME.is_file():
            launch["channel"] = "chrome"
        browser = p.chromium.launch(headless=True, **launch)
        ctx = browser.new_context(viewport=DESKTOP_VIEWPORT)
        page = ctx.new_page()
        yield page
        ctx.close()
        browser.close()


@pytest.fixture
def active_project(browser_page):
    """Seed localStorage with a known project (shell-proto-54fe89: 3 sources,
    6 revisions) so cross-layer tests run against real, queryable layers.
    Function-scoped so each test gets a fresh page state."""
    project_id = "shell-proto-54fe89"
    browser_page.goto(SHELL_BASE + "/index.html", wait_until="domcontentloaded")
    browser_page.evaluate(
        "(id) => localStorage.setItem('assure_project', id)",
        project_id,
    )
    browser_page.reload()
    browser_page.wait_for_selector("#pane-right", state="visible")
    browser_page.evaluate(
        "() => { if (window.SHELL && SHELL.ui && SHELL.ui.selection) "
        "{ SHELL.ui.selection.nodeId = null; } }"
    )
    return project_id


@pytest.fixture
def goto_shell(browser_page):
    def _goto() -> None:
        browser_page.set_default_timeout(20000)
        browser_page.goto(SHELL_BASE + "/index.html", wait_until="domcontentloaded")
        browser_page.wait_for_selector("#pane-right", state="visible")

    return _goto


@pytest.fixture
def fire_intent(browser_page):
    def _fire(text: str) -> None:
        browser_page.fill("#dock-text", text)
        browser_page.press("#dock-text", "Enter")

    return _fire


@pytest.fixture
def wait_for_render(browser_page):
    def _wait(timeout: int = 60000) -> None:
        browser_page.wait_for_function(
            "() => document.querySelectorAll('.doc-draft .jdf-node').length > 0",
            timeout=timeout,
        )

    return _wait


@pytest.fixture
def capture_draft_stream(browser_page):
    """Capture the JSON body of the /draft/stream POST the next intent fires."""
    captured: dict = {"body": None}

    def _handler(route):
        captured["body"] = route.request.post_data
        route.continue_()

    browser_page.route("**/draft/stream", _handler)

    def _get() -> dict:
        if not captured["body"]:
            return {}
        try:
            return json.loads(captured["body"])
        except Exception:
            return {"_raw": captured["body"]}

    yield _get
    try:
        browser_page.unroute("**/draft/stream")
    except Exception:
        pass


@pytest.fixture
def capture_verified_event(browser_page, fire_intent):
    def _capture(text: str, timeout: int = 60000) -> dict | None:
        with browser_page.expect_response("**/draft/stream", timeout=timeout) as ri:
            fire_intent(text)
        raw = ri.value.body()
        return parse_verified(raw)

    return _capture


@pytest.fixture
def read_gate_block():
    def _read(project_id: str) -> dict:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT last_compiled_json FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return {}
        try:
            data = json.loads(row[0])
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        return data.get("gate") or {}

    return _read


@pytest.fixture
def read_revision_count():
    def _count(project_id: str) -> int:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM jdf_revisions WHERE project_id = ?", (project_id,)
            ).fetchone()
        finally:
            conn.close()
        return int(row[0] or 0)

    return _count


@pytest.fixture
def read_substrate_count():
    def _count(project_id: str) -> int:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM substrate_vault WHERE project_id = ?", (project_id,)
            ).fetchone()
        finally:
            conn.close()
        return int(row[0] or 0)

    return _count


@pytest.fixture
def extract_pdf_text(shell_servers_up):
    def _extract(project_id: str) -> str:
        out_pdf = "/tmp/assure-honesty.pdf"
        url = f"{API_BASE}/api/projects/{project_id}/export?format=audit-pdf"
        subprocess.run(["curl", "-s", url, "-o", out_pdf], check=True, timeout=60)
        if not shutil.which("pdftotext"):
            pytest.skip(f"pdftotext not installed; cannot extract {out_pdf}")
        res = subprocess.run(
            ["pdftotext", out_pdf, "-"], capture_output=True, text=True, timeout=60
        )
        return res.stdout

    return _extract
