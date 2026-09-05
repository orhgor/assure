"""SQLite connection and idempotent schema migrations for Assure on EBS."""

from __future__ import annotations

import sqlite3
import time
from typing import Callable, TypeVar

T = TypeVar("T")

DB_LOCKED_MAX_RETRIES = 3
DB_LOCKED_BACKOFF_S = 0.5

try:
    from ..history import _apply_pragmas, _new_connection, get_db
except ImportError:
    from history import _apply_pragmas, _new_connection, get_db

_SCHEMA_VERSION = 13


def _migrate_v10(db: sqlite3.Connection) -> None:
    """Substrate Vault UI: file size for display, included flag for compile grounding."""
    if not _column_exists(db, "substrate_vault", "file_size_bytes"):
        db.execute(
            "ALTER TABLE substrate_vault ADD COLUMN file_size_bytes INTEGER NOT NULL DEFAULT 0"
        )
    if not _column_exists(db, "substrate_vault", "included"):
        db.execute("ALTER TABLE substrate_vault ADD COLUMN included INTEGER NOT NULL DEFAULT 1")


def _migrate_v11(db: sqlite3.Connection) -> None:
    """Per-project source.md + last compiled JDF AST (manifest.json)."""
    if not _column_exists(db, "projects", "source_md"):
        db.execute("ALTER TABLE projects ADD COLUMN source_md TEXT NOT NULL DEFAULT ''")
    if not _column_exists(db, "projects", "last_compiled_json"):
        db.execute("ALTER TABLE projects ADD COLUMN last_compiled_json TEXT NOT NULL DEFAULT '[]'")


def _migrate_v13(db: sqlite3.Connection) -> None:
    """Per-node revision snapshots for surgical history modal."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS node_revisions (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            node_json TEXT NOT NULL,
            document_version INTEGER,
            mutation_type TEXT NOT NULL DEFAULT 'NODE_UPDATE',
            change_summary TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (project_id, node_id, version)
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_node_revisions_lookup
        ON node_revisions(project_id, node_id, version DESC)
        """
    )


def _migrate_v12(db: sqlite3.Connection) -> None:
    """Compile/Red-Hat cache blobs. OMP is the queryable index; this holds large AST JSON."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_cache (
            cache_key TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_pipeline_cache_proj ON pipeline_cache(project_id, kind)"
    )


def _migrate_v5(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_settings (
            project_id TEXT PRIMARY KEY,
            show_citations INTEGER NOT NULL DEFAULT 1,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
        """
    )


def _column_exists(db: sqlite3.Connection, table: str, column: str) -> bool:
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _migrate_v9(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_compile_limits (
            project_id TEXT NOT NULL,
            date TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (project_id, date),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
        """
    )


def _migrate_v8(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS substrates (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            page_count INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL DEFAULT 'edge',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_substrates_proj ON substrates(project_id, created_at DESC)"
    )


def _migrate_v6(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS substrate_vault (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            page_count INTEGER NOT NULL DEFAULT 1,
            extracted_text TEXT NOT NULL,
            tables_json TEXT NOT NULL DEFAULT '[]',
            forms_json TEXT NOT NULL DEFAULT '[]',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_substrate_proj ON substrate_vault(project_id, created_at DESC)"
    )


def _migrate_v4(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS project_comments (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            author TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_comments_proj ON project_comments(project_id, created_at DESC)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_comments_node ON project_comments(project_id, node_id)"
    )


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
    db.execute("CREATE INDEX IF NOT EXISTS idx_metrics_created ON system_metrics(created_at DESC)")

    _migrate_v3(db)
    if current < 4:
        pass
    if current < 5:
        _migrate_v4(db)
    if current < 6:
        _migrate_v5(db)
    if current < 7:
        _migrate_v6(db)
    if current < 8:
        _migrate_v8(db)
    if current < 9:
        _migrate_v9(db)
    if current < 10:
        _migrate_v10(db)
    if current < 11:
        _migrate_v11(db)
    if current < 12:
        _migrate_v12(db)
    if current < 13:
        _migrate_v13(db)

    if current < _SCHEMA_VERSION:
        for version in range(current + 1, _SCHEMA_VERSION + 1):
            db.execute(
                "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
                (version,),
            )

    run_with_db_retry(db.commit)


def _connect_with_retry() -> sqlite3.Connection:
    """Open SQLite with retry on database locked."""
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(DB_LOCKED_MAX_RETRIES):
        conn: sqlite3.Connection | None = None
        try:
            conn = _new_connection()
            _apply_pragmas(conn)
            conn.execute("SELECT 1")
            return conn
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            if "locked" not in str(exc).lower() or attempt >= DB_LOCKED_MAX_RETRIES - 1:
                raise
            time.sleep(DB_LOCKED_BACKOFF_S * (attempt + 1))
    if last_exc is not None:
        raise last_exc
    raise sqlite3.OperationalError("database is locked")


def run_with_db_retry(
    fn: Callable[..., T],
    *args,
    max_retries: int = DB_LOCKED_MAX_RETRIES,
    backoff_s: float = DB_LOCKED_BACKOFF_S,
    **kwargs,
) -> T:
    """Retry a SQLite operation when the database is locked."""
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if "locked" not in str(exc).lower() or attempt >= max_retries - 1:
                raise
            time.sleep(backoff_s * (attempt + 1))
    if last_exc is not None:
        raise last_exc
    raise sqlite3.OperationalError("database is locked")


def open_connection() -> sqlite3.Connection:
    return _connect_with_retry()
