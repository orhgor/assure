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


ORPHAN_TABLES = (
    "audit_log",
    "jdf_documents",
    "node_revisions",
    "pipeline_cache",
    "project_budgets",
    "token_ledger_entries",
    "user_activity_log",
)


def test_fresh_database_declares_the_project_fk(tmp_path):
    """The seven tables a project delete orphans carry the cascade in the DDL.

    A database created from scratch — CI, a new install — has to match the schema
    scripts/aws/migrate_fk_constraints.py produced on the boxes that predate it,
    or the constraint exists on one and not the other.
    """
    conn = sqlite3.connect(str(tmp_path / "history.sqlite"))
    conn_mod.init_db(conn)
    for table in ORPHAN_TABLES:
        fks = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        assert len(fks) == 1, (table, fks)
        # (id, seq, table, from, to, on_update, on_delete, match)
        assert (fks[0][2], fks[0][3], fks[0][4], fks[0][6]) == (
            "projects",
            "project_id",
            "id",
            "CASCADE",
        ), (table, fks)
    conn.close()


def test_project_delete_leaves_no_orphans_on_a_fresh_database(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "history.sqlite"))
    conn_mod.init_db(conn)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("INSERT INTO projects (id, title) VALUES ('p1', 'p1')")
    conn.execute(
        "INSERT INTO audit_log (id, request_id, project_id, action, success)"
        " VALUES ('a1', 'r1', 'p1', 'DRAFT_STREAM', 1)"
    )
    conn.execute(
        "INSERT INTO jdf_documents (project_id, document_id, tree_json)"
        " VALUES ('p1', 'doc1', '{}')"
    )
    conn.execute(
        "INSERT INTO node_revisions (id, project_id, node_id, version, node_json)"
        " VALUES ('n1', 'p1', 'node1', 1, '{}')"
    )
    conn.execute(
        "INSERT INTO pipeline_cache (cache_key, project_id, kind, payload_json)"
        " VALUES ('c1', 'p1', 'compile', '{}')"
    )
    conn.execute("INSERT INTO project_budgets (project_id) VALUES ('p1')")
    conn.execute(
        "INSERT INTO token_ledger_entries (project_id, task_type, model_id)"
        " VALUES ('p1', 'compile', 'm1')"
    )
    conn.execute(
        "INSERT INTO user_activity_log (id, user_id, project_id, action)"
        " VALUES ('u1', 'user1', 'p1', 'PROJECT_CREATE')"
    )
    conn.commit()
    before = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ORPHAN_TABLES}
    assert before == {t: 1 for t in ORPHAN_TABLES}

    conn.execute("DELETE FROM projects WHERE id = 'p1'")
    conn.commit()

    after = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ORPHAN_TABLES}
    assert after == {t: 0 for t in ORPHAN_TABLES}
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
