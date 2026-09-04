"""Shared pytest fixtures for Assure workbench integration tests."""

from __future__ import annotations

import socket
import sqlite3
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from werkzeug.serving import make_server


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def live_assure_server():
    """Start a real Flask app on a random port (no auth) for Playwright."""
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


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "playwright: browser-driven Assure workbench simulation (requires pytest-playwright)",
    )
