"""Health diagnostic endpoint tests."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from prompt_matrix.db.connection import init_db


@pytest.fixture
def health_client():
    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "history.sqlite"

    def _getter():
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    class _StatVfs:
        f_frsize = 4096
        f_bavail = 2_000_000

    with (
        patch("prompt_matrix.history.DB_PATH", db_path),
        patch("prompt_matrix.lib.logger.DB_PATH", db_path),
        patch("prompt_matrix.lib.logger.resolve_db_path", return_value=str(db_path)),
        patch("prompt_matrix.db.connection.get_db", side_effect=_getter),
        patch("prompt_matrix.routers.health.os.statvfs", return_value=_StatVfs()),
    ):
        conn = _getter()
        init_db(conn)
        from prompt_matrix.web import create_app

        app = create_app(require_auth=False)
        with app.test_client() as client:
            yield client
        conn.close()
        tmp.cleanup()


def test_health_returns_ok(health_client):
    response = health_client.get("/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["status"] == "healthy"
    assert payload["checks"]["db"] == "ok"
    # One-release alias: the probe is PostgreSQL, the old key still answers.
    assert payload["checks"]["sqlite"] == payload["checks"]["db"]
    assert "disk_free_gb" in payload["checks"]
    assert payload["checks"]["disk"] == "ok"
    assert "backup" in payload["checks"]
    assert payload["ui"]["jdf_workbench"] is True
    assert "css_version" in payload["ui"]
    assert "js_version" in payload["ui"]
