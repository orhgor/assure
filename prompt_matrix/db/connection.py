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

_SCHEMA_VERSION = 20


def _migrate_v20(db: sqlite3.Connection) -> None:
    """Big Four ICP templates — research dossier + refreshed copy."""
    import json as _json

    def _structure(template_id: str, fallback: dict) -> dict:
        row = db.execute(
            "SELECT jdf_structure FROM project_templates WHERE id = ?",
            (template_id,),
        ).fetchone()
        if not row or not row[0]:
            return fallback
        try:
            return _json.loads(row[0])
        except (TypeError, ValueError):
            return fallback

    research_dossier = {
        "body": [
            {
                "type": "section",
                "id": "sec-summary",
                "title": "Executive Summary",
                "children": [],
                "meta": {},
                "annotations": {"redhat": [], "z3": []},
            },
            {
                "type": "section",
                "id": "sec-sources",
                "title": "Source Map",
                "children": [],
                "meta": {},
                "annotations": {"redhat": [], "z3": []},
            },
            {
                "type": "section",
                "id": "sec-citations",
                "title": "Verified Citations",
                "children": [],
                "meta": {},
                "annotations": {"redhat": [], "z3": []},
            },
        ]
    }
    contract_default = {
        "body": [
            {
                "type": "section",
                "id": "sec-parties",
                "title": "Parties & Scope",
                "children": [],
                "meta": {},
                "annotations": {"redhat": [], "z3": []},
            },
            {
                "type": "section",
                "id": "sec-risks",
                "title": "Risk Summary",
                "children": [],
                "meta": {},
                "annotations": {"redhat": [], "z3": []},
            },
        ]
    }
    compliance_default = {
        "body": [
            {
                "type": "section",
                "id": "sec-compliance",
                "title": "Executive Summary",
                "children": [],
                "meta": {},
                "annotations": {"redhat": [], "z3": []},
            },
            {
                "type": "section",
                "id": "sec-findings",
                "title": "Findings",
                "children": [],
                "meta": {},
                "annotations": {"redhat": [], "z3": []},
            },
        ]
    }

    rows = [
        (
            "research-dossier",
            "Research Dossier",
            research_dossier,
            (
                "Synthesize scattered findings, map each claim to primary sources, "
                "and verify citations before publishing."
            ),
            ["sources.zip"],
        ),
        (
            "compliance-memo",
            "Compliance Memo",
            _structure("compliance-memo", compliance_default),
            (
                "Audit regulatory filings, policies, and statutory statements "
                "against binding guidelines."
            ),
            ["policy-handbook.pdf"],
        ),
        (
            "contract-review",
            "Contract Review",
            _structure("contract-review", contract_default),
            "Cross-check terms, redlines, and commitments across multi-party agreements.",
            ["contract.pdf"],
        ),
        ("blank", "Blank Workspace", {"body": []}, "", []),
    ]
    for tid, name, structure, prompt, sources in rows:
        db.execute(
            """
            INSERT INTO project_templates
              (id, name, jdf_structure, default_prompt, suggested_sources)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              name = excluded.name,
              jdf_structure = excluded.jdf_structure,
              default_prompt = excluded.default_prompt,
              suggested_sources = excluded.suggested_sources
            """,
            (tid, name, _json.dumps(structure), prompt, _json.dumps(sources)),
        )


def _migrate_v19(db: sqlite3.Connection) -> None:
    """User feedback table for Resend + analytics."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id TEXT PRIMARY KEY,
            user_email TEXT,
            message TEXT NOT NULL,
            rating INTEGER,
            url TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at DESC)")


def _migrate_v18(db: sqlite3.Connection) -> None:
    """v2.0: analytics SQL views."""
    try:
        from .analytics_views import ensure_analytics_views
    except ImportError:
        from db.analytics_views import ensure_analytics_views
    ensure_analytics_views(db)


def _migrate_v17(db: sqlite3.Connection) -> None:
    """v1.5: sign-offs, document locks, user activity audit log."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS sign_offs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            node_id TEXT,
            reviewer_id TEXT NOT NULL,
            reviewer_name TEXT,
            status TEXT CHECK(status IN ('approved','rejected','pending')),
            comment TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_sign_offs_project ON sign_offs(project_id, timestamp DESC)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS document_locks (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            locked_by TEXT NOT NULL,
            locked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            content_hash TEXT NOT NULL
        )
        """
    )
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_doc_locks_proj_ver ON document_locks(project_id, version)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_activity_log (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            project_id TEXT,
            action TEXT NOT NULL,
            details TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_activity_proj ON user_activity_log(project_id, timestamp DESC)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_activity_user ON user_activity_log(user_id, timestamp DESC)"
    )


def _migrate_v16(db: sqlite3.Connection) -> None:
    """v1.4: project templates + SQLite prompt library."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS project_templates (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            jdf_structure TEXT NOT NULL DEFAULT '{}',
            default_prompt TEXT NOT NULL DEFAULT '',
            suggested_sources TEXT NOT NULL DEFAULT '[]',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS prompts (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            name TEXT NOT NULL,
            class TEXT NOT NULL DEFAULT 'research',
            tags TEXT NOT NULL DEFAULT '[]',
            content TEXT NOT NULL DEFAULT '',
            version_history TEXT NOT NULL DEFAULT '[]',
            is_global INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_prompts_user ON prompts(user_id, updated_at DESC)")

    seeds = [
        (
            "compliance-memo",
            "Compliance Memo",
            {
                "body": [
                    {
                        "type": "section",
                        "id": "sec-compliance",
                        "title": "Executive Summary",
                        "children": [
                            {
                                "type": "paragraph",
                                "id": "p-compliance-1",
                                "content": "",
                                "meta": {},
                                "annotations": {"redhat": [], "z3": []},
                            }
                        ],
                        "meta": {},
                        "annotations": {"redhat": [], "z3": []},
                    },
                    {
                        "type": "section",
                        "id": "sec-findings",
                        "title": "Findings",
                        "children": [],
                        "meta": {},
                        "annotations": {"redhat": [], "z3": []},
                    },
                ]
            },
            "Draft a compliance memo summarizing regulatory obligations, key risks, and recommended controls.",
            ["policy-handbook.pdf"],
        ),
        (
            "research-paper",
            "Research Paper",
            {
                "body": [
                    {
                        "type": "section",
                        "id": "sec-abstract",
                        "title": "Abstract",
                        "children": [],
                        "meta": {},
                        "annotations": {"redhat": [], "z3": []},
                    },
                    {
                        "type": "section",
                        "id": "sec-methods",
                        "title": "Methods",
                        "children": [],
                        "meta": {},
                        "annotations": {"redhat": [], "z3": []},
                    },
                    {
                        "type": "section",
                        "id": "sec-results",
                        "title": "Results",
                        "children": [],
                        "meta": {},
                        "annotations": {"redhat": [], "z3": []},
                    },
                ]
            },
            "Outline a research paper with abstract, methods, results, and discussion for the uploaded sources.",
            ["paper-draft.pdf"],
        ),
        (
            "blank",
            "Blank",
            {"body": []},
            "",
            [],
        ),
        (
            "contract-review",
            "Contract Review",
            {
                "body": [
                    {
                        "type": "section",
                        "id": "sec-parties",
                        "title": "Parties & Scope",
                        "children": [],
                        "meta": {},
                        "annotations": {"redhat": [], "z3": []},
                    },
                    {
                        "type": "section",
                        "id": "sec-risks",
                        "title": "Risk Summary",
                        "children": [],
                        "meta": {},
                        "annotations": {"redhat": [], "z3": []},
                    },
                ]
            },
            "Review the contract and list obligations, termination clauses, and liability risks.",
            ["contract.pdf"],
        ),
        (
            "blog-post",
            "Blog Post",
            {
                "body": [
                    {
                        "type": "section",
                        "id": "sec-hook",
                        "title": "Hook",
                        "children": [],
                        "meta": {},
                        "annotations": {"redhat": [], "z3": []},
                    },
                    {
                        "type": "section",
                        "id": "sec-body",
                        "title": "Body",
                        "children": [],
                        "meta": {},
                        "annotations": {"redhat": [], "z3": []},
                    },
                ]
            },
            "Write a concise blog post with a strong hook, three supporting points, and a clear call to action.",
            [],
        ),
    ]
    import json as _json

    for tid, name, structure, prompt, sources in seeds:
        db.execute(
            """
            INSERT OR IGNORE INTO project_templates
              (id, name, jdf_structure, default_prompt, suggested_sources)
            VALUES (?, ?, ?, ?, ?)
            """,
            (tid, name, _json.dumps(structure), prompt, _json.dumps(sources)),
        )

    starter_prompts = [
        (
            "starter-executive-summary",
            "Executive summary",
            "research",
            "Write a one-page executive summary with verified metrics and plain-language risks.",
        ),
        (
            "starter-policy-brief",
            "Policy brief",
            "research",
            "Draft a policy brief: context, options, recommendation, and citations from Sources.",
        ),
    ]
    for pid, name, pclass, content in starter_prompts:
        db.execute(
            """
            INSERT OR IGNORE INTO prompts
              (id, user_id, name, class, tags, content, version_history, is_global)
            VALUES (?, NULL, ?, ?, '[]', ?, '[{"version":1}]', 1)
            """,
            (pid, name, pclass, content),
        )


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


def _migrate_v14(db: sqlite3.Connection) -> None:
    """Project owner and pipeline cache TTL."""
    if not _column_exists(db, "projects", "owner_id"):
        db.execute("ALTER TABLE projects ADD COLUMN owner_id TEXT")
    if not _column_exists(db, "pipeline_cache", "expires_at"):
        db.execute("ALTER TABLE pipeline_cache ADD COLUMN expires_at DATETIME")
        db.execute(
            """
            UPDATE pipeline_cache
            SET expires_at = datetime(updated_at, '+30 days')
            WHERE expires_at IS NULL
            """
        )
    db.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_cache_exp ON pipeline_cache(expires_at)")


def _migrate_v15(db: sqlite3.Connection) -> None:
    """Keyword-level source conflict flags (not semantic NLI)."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS source_conflicts (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            claim_id TEXT NOT NULL,
            source_a_id TEXT NOT NULL,
            source_b_id TEXT NOT NULL,
            conflict_description TEXT NOT NULL,
            detected_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_source_conflicts_proj
        ON source_conflicts(project_id, claim_id)
        """
    )


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
    if current < 14:
        _migrate_v14(db)
    if current < 15:
        _migrate_v15(db)
    if current < 16:
        _migrate_v16(db)
    if current < 17:
        _migrate_v17(db)
    if current < 18:
        _migrate_v18(db)
    if current < 19:
        _migrate_v19(db)
    if current < 20:
        _migrate_v20(db)

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


try:
    from sqlalchemy.exc import OperationalError as SAOperationalError
except ImportError:
    SAOperationalError = None  # type: ignore[misc, assignment]


def execute_write_with_retry(fn, max_retries: int = 5, base_delay: float = 0.2):
    """
    Execute a database write with exponential backoff on lock/busy errors.
    Handles raw sqlite3 and SQLAlchemy-wrapped exceptions.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            is_sqlite_lock = isinstance(exc, sqlite3.OperationalError) and any(
                term in str(exc).lower() for term in ("locked", "busy")
            )
            is_sa_lock = (
                SAOperationalError is not None
                and isinstance(exc, SAOperationalError)
                and any(term in str(exc).lower() for term in ("locked", "busy"))
            )
            if (is_sqlite_lock or is_sa_lock) and attempt < max_retries - 1:
                time.sleep(base_delay * (2**attempt))
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise sqlite3.OperationalError("database is locked")
