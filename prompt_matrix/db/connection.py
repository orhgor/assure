"""SQLite connection and idempotent schema migrations for Assure on EBS."""

from __future__ import annotations

import sqlite3

try:
    from ..history import _apply_pragmas, _new_connection, get_db
except ImportError:
    from history import _apply_pragmas, _new_connection, get_db

_SCHEMA_VERSION = 4


def _column_exists(db: sqlite3.Connection, table: str, column: str) -> bool:
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _migrate_v3(db: sqlite3.Connection) -> None:
    if not _column_exists(db, "project_budgets", "last_reset"):
        db.execute(
            "ALTER TABLE project_budgets ADD COLUMN last_reset TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        )
    if _column_exists(db, "token_ledger_entries", "meta"):
        if not _column_exists(db, "token_ledger_entries", "cache_read_tokens"):
            db.execute(
                "ALTER TABLE token_ledger_entries ADD COLUMN cache_read_tokens INTEGER DEFAULT 0"
            )
        if not _column_exists(db, "token_ledger_entries", "cache_write_tokens"):
            db.execute(
                "ALTER TABLE token_ledger_entries ADD COLUMN cache_write_tokens INTEGER DEFAULT 0"
            )
        if not _column_exists(db, "token_ledger_entries", "timestamp"):
            db.execute(
                "ALTER TABLE token_ledger_entries ADD COLUMN timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
            )


def init_db(conn: sqlite3.Connection | None = None) -> None:
    """Create or migrate tables on startup. Safe to call repeatedly."""
    db = conn or get_db()
    db.execute("PRAGMA journal_mode=WAL;")
    db.execute("PRAGMA busy_timeout=5000;")
    _apply_pragmas(db)

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    row = db.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    current = int(row[0]) if row and row[0] is not None else 0

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            current_version INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS jdf_revisions (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            jdf_tree TEXT NOT NULL,
            truth_ledger TEXT NOT NULL,
            mutation_type TEXT NOT NULL,
            target_node_id TEXT,
            change_summary TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            UNIQUE (project_id, version)
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_jdf_proj
        ON jdf_revisions(project_id, version DESC)
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS project_budgets (
            project_id TEXT PRIMARY KEY,
            token_limit INTEGER NOT NULL DEFAULT 250000,
            tokens_used INTEGER NOT NULL DEFAULT 0,
            last_reset DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS token_ledger_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            task_type TEXT NOT NULL,
            model_id TEXT NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_write_tokens INTEGER DEFAULT 0,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            meta TEXT
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS jdf_documents (
            project_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            tree_json TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id TEXT PRIMARY KEY DEFAULT (hex(randomblob(16))),
            request_id TEXT NOT NULL,
            project_id TEXT,
            action TEXT NOT NULL,
            target_node_id TEXT,
            success INTEGER NOT NULL,
            duration_ms INTEGER,
            error_type TEXT,
            error_message TEXT,
            details TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_audit_req ON audit_log(request_id)")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_proj ON audit_log(project_id, created_at DESC)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS system_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cpu_percent REAL NOT NULL,
            memory_used_mb REAL NOT NULL,
            memory_total_mb REAL NOT NULL,
            swap_used_mb REAL NOT NULL,
            disk_used_percent REAL NOT NULL,
            disk_free_gb REAL NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_metrics_created ON system_metrics(created_at DESC)"
    )

    _migrate_v3(db)

    if current < _SCHEMA_VERSION:
        for version in range(current + 1, _SCHEMA_VERSION + 1):
            db.execute(
                "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
                (version,),
            )

    db.commit()


def open_connection() -> sqlite3.Connection:
    conn = _new_connection()
    _apply_pragmas(conn)
    return conn
