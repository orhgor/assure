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
        # shutil.disk_usage (the branch taken when ASSURE_DATA_DIR does not
        # exist yet) reads these three as well.
        f_blocks = 4_000_000
        f_bfree = 2_000_000
        f_bsize = 4096

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
    assert payload["status"] == "healthy", payload["checks"]
    assert payload["checks"]["db"] == "ok"
    # One-release alias: the probe is PostgreSQL, the old key still answers.
    assert payload["checks"]["sqlite"] == payload["checks"]["db"]
    assert "disk_free_gb" in payload["checks"]
    assert payload["checks"]["disk"] == "ok"
    assert "backup" in payload["checks"]
    assert payload["ui"]["jdf_workbench"] is True
    assert "css_version" in payload["ui"]
    assert "js_version" in payload["ui"]


def test_health_models_check_reads_ollama_inventory(health_client, monkeypatch):
    """With the local backend the daemon's tag list decides: both configured
    models present → ok; one absent → degraded (2026-09-25: the EC2 box runs
    without provider keys, so a missing model file is a real outage)."""
    import io
    import json as _json
    import urllib.request

    monkeypatch.setenv("ASSURE_LLM_BACKEND", "ollama")
    monkeypatch.setenv("ASSURE_OLLAMA_MODEL", "qwen2.5:1.5b")
    monkeypatch.setenv("ASSURE_OLLAMA_MODEL_B", "llama3.2:1b")
    monkeypatch.setenv("OLLAMA_API_BASE", "http://ollama.test:11434")

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    import urllib.error

    def fake_open(url, timeout=None):
        if str(url) != "http://ollama.test:11434/api/tags":
            # other probes in /health that reach for the network stay "down" here
            raise urllib.error.URLError("offline under test")
        return _Resp(_json.dumps({"models": [{"name": "qwen2.5:1.5b"}, {"name": "llama3.2:1b"}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    payload = health_client.get("/health").get_json()
    assert payload["checks"]["models"] == {
        "backend": "ollama",
        "api_base": "http://ollama.test:11434",
        "status": "ok",
        "present": ["qwen2.5:1.5b", "llama3.2:1b"],
        "missing": [],
    }
    assert payload["status"] == "healthy", payload["checks"]

    def fake_open_missing(url, timeout=None):
        if str(url) != "http://ollama.test:11434/api/tags":
            raise urllib.error.URLError("offline under test")
        return _Resp(_json.dumps({"models": [{"name": "qwen2.5:1.5b:latest"}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_open_missing)
    payload = health_client.get("/health").get_json()
    assert payload["checks"]["models"]["missing"] == ["llama3.2:1b"]
    assert payload["checks"]["models"]["status"] == "missing"
    assert payload["status"] == "degraded" and payload["degraded"] is True


def test_health_models_check_not_local_for_cloud_backend(health_client, monkeypatch):
    monkeypatch.setenv("ASSURE_LLM_BACKEND", "")
    payload = health_client.get("/health").get_json()
    assert payload["checks"]["models"] == {"backend": "cloud", "status": "not local"}
