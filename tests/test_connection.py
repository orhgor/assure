"""SQLite connection retry helpers."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from prompt_matrix.db import connection as conn_mod


def test_open_connection_retries_on_locked():
    good = MagicMock()
    good.execute.return_value = None
    locked = sqlite3.OperationalError("database is locked")

    with patch.object(conn_mod, "_new_connection", side_effect=[locked, locked, good]):
        with patch.object(conn_mod, "_apply_pragmas"):
            with patch("prompt_matrix.db.connection.time.sleep") as sleep:
                result = conn_mod.open_connection()

    assert result is good
    assert sleep.call_count == 2


def test_run_with_db_retry_commits_on_third_attempt():
    locked = sqlite3.OperationalError("database is locked")
    fn = MagicMock(side_effect=[locked, locked, "ok"])

    with patch("prompt_matrix.db.connection.time.sleep") as sleep:
        assert conn_mod.run_with_db_retry(fn) == "ok"

    assert fn.call_count == 3
    assert sleep.call_count == 2
