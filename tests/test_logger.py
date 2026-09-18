"""Audit logger fail-safe and SQLite persistence tests."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from prompt_matrix.db.connection import init_db
from prompt_matrix.lib.logger import AuditLogger, audit_drop_count


@pytest.fixture
def audit_db():
    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "history.sqlite"
    conn = sqlite3.connect(str(db_path))
    init_db(conn)
    conn.close()
    yield str(db_path)
    tmp.cleanup()


def test_log_audit_inserts_row(audit_db):
    audit = AuditLogger(audit_db)
    audit.log_audit(
        "req-123",
        "proj-1",
        "INQUIRE_STREAM",
        success=True,
        duration_ms=42,
        details={"model": "test-model"},
    )
    conn = sqlite3.connect(audit_db)
    row = conn.execute(
        "SELECT request_id, project_id, action, success, duration_ms FROM audit_log"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "req-123"
    assert row[1] == "proj-1"
    assert row[2] == "INQUIRE_STREAM"
    assert row[3] == 1
    assert row[4] == 42


def test_log_audit_does_not_raise_on_db_failure():
    audit = AuditLogger("/nonexistent/nested/bad/history.sqlite")
    audit.log_audit("req-fail", "proj-x", "BACKUP", success=False, error_message="disk full")


def test_dropped_audit_row_is_counted_and_a_written_one_is_not(audit_db):
    """A row that cannot be written is counted; a row that is written is not.

    The count is process-wide, so the assertions are deltas around each write.
    """
    before = audit_drop_count()

    AuditLogger("/nonexistent/nested/bad/history.sqlite").log_audit(
        "req-drop", "proj-x", "BACKUP", success=False
    )
    assert audit_drop_count() == before + 1

    audit = AuditLogger(audit_db)
    audit.log_audit("req-ok", "proj-1", "INQUIRE_STREAM", success=True)
    assert audit_drop_count() == before + 1

    conn = sqlite3.connect(audit_db)
    written = conn.execute("SELECT count(*) FROM audit_log WHERE request_id='req-ok'").fetchone()[0]
    conn.close()
    assert written == 1
