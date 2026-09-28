"""scripts/backup_db.py: a backup is a pg_dump the object store holds and an
audit row /health can read; a failed run is an audit row with the error."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "backup.sqlite"))
    monkeypatch.setenv("ASSURE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ASSURE_S3_BUCKET", "")
    import prompt_matrix.history as history_mod
    history_mod.DB_PATH = history_mod._resolve_db_path()
    from prompt_matrix.db.connection import init_db
    init_db()
    return tmp_path


def test_backup_writes_a_dump_and_an_audit_row(env, monkeypatch):
    import backup_db

    def fake_run(cmd, capture_output, text, timeout):
        assert cmd[0] == "pg_dump" and "--format=custom" in cmd
        Path(cmd[-2].split("=", 1)[1]).write_bytes(b"PGDMP-fake")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)
    result = backup_db.run_backup()
    assert result["ok"] and result["key"].startswith("backups/postgres/") and result["bytes"] == 10
    assert (env / "objects" / result["key"]).exists() or list(env.rglob("*.dump"))
    from prompt_matrix.history import get_db
    row = get_db().execute("SELECT success FROM audit_log WHERE action='BACKUP' ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row is not None and int(row[0]) == 1


def test_failed_dump_is_recorded_not_raised(env, monkeypatch):
    import backup_db

    monkeypatch.setattr(backup_db.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "connection refused"))
    result = backup_db.run_backup()
    assert result["ok"] is False and "connection refused" in result["error"]
    from prompt_matrix.history import get_db
    row = get_db().execute("SELECT success, error_message FROM audit_log WHERE action='BACKUP' ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row is not None and int(row[0]) == 0 and "connection refused" in str(row[1])
