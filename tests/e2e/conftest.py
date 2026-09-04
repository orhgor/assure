"""Shared pytest fixtures for Assure workbench integration tests."""

from __future__ import annotations

import os
import socket
import sqlite3
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from werkzeug.serving import make_server

# CSS @media (max-width: 1024px) shows #mobile-lockout and hides #assure-app.
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
        patch("prompt_matrix.lib.logger.DB_PATH", db_path),
        patch("prompt_matrix.lib.logger.resolve_db_path", return_value=str(db_path)),
        patch("prompt_matrix.db.connection.get_db", side_effect=_getter),
        patch("prompt_matrix.routers.health.os.statvfs", return_value=_StatVfs()),
        # Blank Clerk keys so auth is off for the Playwright server only.
        # Do not set ASSURE_REQUIRE_LOGIN=false here — that leaks into unrelated tests.
        patch.dict(
            os.environ,
            {"CLERK_PUBLISHABLE_KEY": "", "CLERK_SECRET_KEY": ""},
            clear=False,
        ),
    ]
    for item in patches:
        item.start()

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
