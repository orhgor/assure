"""Idempotent schema migrations and connection helpers (PostgreSQL via db/pg_compat)."""

from __future__ import annotations

import threading
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, TypeVar

T = TypeVar("T")

DB_LOCKED_MAX_RETRIES = 3
DB_LOCKED_BACKOFF_S = 0.5

try:
    from ..db.open_connections import note_connection_open
    from ..history import (
        _apply_pragmas,
        _new_connection,
        _release_direct_connection,
        _resolve_db_path,
        _rollback_quietly,
        db_scope,
        get_db,
    )
except ImportError:
    from db.open_connections import note_connection_open
    from history import (
        _apply_pragmas,
        _new_connection,
        _release_direct_connection,
        _resolve_db_path,
        _rollback_quietly,
        db_scope,
        get_db,
    )

# The migration counter. It gates the _migrate_vN functions below against the
# schema_migrations table, so it moves only when a new migration step is added.
#
# The seven FOREIGN KEY clauses on the tables a project delete orphans — audit_log,
# jdf_documents, node_revisions, pipeline_cache, project_budgets,
# token_ledger_entries, user_activity_log — are declarations inside
# CREATE TABLE IF NOT EXISTS. They reach a database that does not have the table
# yet (a fresh install, CI, this repo's tests) and leave an existing one alone,
# where the constraint comes from scripts/aws/migrate_fk_constraints.py instead.
# SQLite cannot add a foreign key to an existing table, so there is no migration
# step to write and the version stays where it is: bumping it would either do
# nothing or record a step that never ran.
_SCHEMA_VERSION = 32


def _migrate_v32(db: sqlite3.Connection) -> None:
    """Parsure V1 review backend (spec ``assure_parsure_v1_icp_spec.md`` §3, §9
    items 19–27): one intake report per parsed document, the corrections and
    disputes reviewers make against its fields, and the audit events. The
    report itself is one JSON document (``report_json``) because its shape is
    the versioned multimodal contract and changes with ``schema_version``; the
    scalar columns beside it exist only so listing and ``analytics`` are a
    SELECT, not a JSON parse of every row. Spec §9 item 22 says "SQLite" for
    the audit log; this repository is PostgreSQL-only (CLAUDE.md), so the log
    lives here in ``parsure_audit_events``.
    """
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS parsure_reports (
            report_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            document_id TEXT,
            revision_id TEXT,
            job_id TEXT,
            filename TEXT NOT NULL DEFAULT '',
            document_type TEXT,
            document_quality_score REAL,
            fields_total INTEGER NOT NULL DEFAULT 0,
            fields_review INTEGER NOT NULL DEFAULT 0,
            fields_rejected INTEGER NOT NULL DEFAULT 0,
            replay_eligible INTEGER NOT NULL DEFAULT 0,
            report_json TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_parsure_reports_project_created ON parsure_reports (project_id, created_at)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS parsure_corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            field_name TEXT NOT NULL,
            original_value TEXT,
            corrected_value TEXT,
            actor TEXT,
            reason TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (report_id) REFERENCES parsure_reports(report_id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_parsure_corrections_report ON parsure_corrections (report_id, field_name)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS parsure_disputes (
            dispute_id TEXT PRIMARY KEY,
            report_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            field_name TEXT NOT NULL,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            due_at DATETIME,
            resolved_at DATETIME,
            resolution TEXT,
            actor TEXT,
            FOREIGN KEY (report_id) REFERENCES parsure_reports(report_id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_parsure_disputes_project_status ON parsure_disputes (project_id, status)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS parsure_audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            report_id TEXT,
            event_type TEXT NOT NULL,
            field_name TEXT,
            actor TEXT,
            payload_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_parsure_audit_project_created ON parsure_audit_events (project_id, created_at)"
    )


def _migrate_v31(db: sqlite3.Connection) -> None:
    """``substrate_vault.scan_version``: which instruction scan produced the
    stored ``instruction_like``/``instruction_hits`` (``compile_guard.scan_version``).
    Empty = unknown → rescanned and stamped on the next list."""
    if not _column_exists(db, "substrate_vault", "scan_version"):
        db.execute("ALTER TABLE substrate_vault ADD COLUMN scan_version TEXT NOT NULL DEFAULT ''")


def _migrate_v30(db: sqlite3.Connection) -> None:
    """Partial index for the stale-job sweep (``ingest_jobs_repository.mark_stale``):
    ``WHERE status NOT IN (done, failed, skipped) AND updated_at < …`` had no
    usable index and scanned the table on every sweep (audit 2026-09-24)."""
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_ingest_jobs_active_updated ON ingest_jobs (updated_at) "
        "WHERE status NOT IN ('done', 'failed', 'skipped')"
    )


def _migrate_v29(db: sqlite3.Connection) -> None:
    """Usage counters: metered external calls per period, for hard caps.

    First user: the Textract spend cap (``services/textract_budget``). One row
    per (name, period) — e.g. ``("textract", "2026-09")`` — with the unit count
    and the list-price cost, incremented atomically with ``ON CONFLICT`` so two
    workers cannot both squeeze under the cap. Shared by every replica because
    it lives in PostgreSQL, not in a process.
    """
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_counters (
            name TEXT NOT NULL,
            period TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (name, period)
        )
        """
    )


def _migrate_v28(db: sqlite3.Connection) -> None:
    """Integration settings: operator-entered configuration for external systems.

    The AWS/S3 integration can be configured from the workbench (Sources panel)
    instead of only from ``.env``; the entered access key id, region, bucket and
    prefix are stored here and the secret is Fernet-encrypted
    (``services/aws_integration``). One row per integration name; the value is a
    JSON document so a new integration needs no new table.
    """
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS integration_settings (
            name TEXT PRIMARY KEY,
            value_json TEXT NOT NULL DEFAULT '{}',
            secret_enc TEXT,
            updated_by TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _migrate_v27(db: sqlite3.Connection) -> None:
    """Ingest jobs: one row per document going through the parse pipeline.

    The web tier answers an upload with 202 and a task id; the worker does the
    work. Until now the only trace of that work was the Celery result (which
    expires) and the final revision (which says nothing about how it got
    there). This table is the durable record a user and an operator can watch:
    which stage the document is in, which parser took it, what OCR confidence
    it produced, what Z3 and Red-Hat said, how long each step took, and — when
    it failed — why. Rows outlive the task result and survive a worker restart.
    """
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS ingest_jobs (
            job_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'import_pdf',
            filename TEXT NOT NULL DEFAULT '',
            object_key TEXT,
            task_id TEXT,
            status TEXT NOT NULL DEFAULT 'queued',
            stage_history TEXT NOT NULL DEFAULT '[]',
            parser_name TEXT,
            source_kind TEXT,
            page_count INTEGER,
            parse_confidence REAL,
            ocr_confidence REAL,
            z3_status TEXT,
            z3_violation_count INTEGER,
            redhat_status TEXT,
            revision_id TEXT,
            revision_version INTEGER,
            omp_artifact_id TEXT,
            substrate_file_id TEXT,
            error TEXT,
            worker TEXT,
            size_bytes INTEGER,
            duration_ms INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            started_at DATETIME,
            finished_at DATETIME,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_ingest_jobs_project ON ingest_jobs(project_id, created_at DESC)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_ingest_jobs_task ON ingest_jobs(task_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_ingest_jobs_status ON ingest_jobs(project_id, status)")


def _migrate_v26(db: sqlite3.Connection) -> None:
    """Substrate Vault: parse metadata + OMP artifact linkage per row.

    JDF CI is the default PDF parser and every parse carries structured
    content and confidence. Persisting parser_name/source_kind/counts/asset
    summary and the staged OMP artifact id on the row means a re-read of the
    vault shows how a document was parsed and where its OMP artifact lives,
    instead of losing all of that the moment ingest returns. parse/OCR
    confidence are REALs or NULL — NULL is the honest unknown, never 0.
    """
    for column, ddl in (
        ("parser_name", "TEXT"),
        ("source_kind", "TEXT"),
        ("parse_confidence", "REAL"),
        ("ocr_confidence", "REAL"),
        ("table_count", "INTEGER"),
        ("image_count", "INTEGER"),
        ("figure_count", "INTEGER"),
        ("asset_summary", "TEXT NOT NULL DEFAULT '{}'"),
        ("omp_artifact_id", "TEXT"),
    ):
        if not _column_exists(db, "substrate_vault", column):
            db.execute(f"ALTER TABLE substrate_vault ADD COLUMN {column} {ddl}")


def _migrate_v25(db: sqlite3.Connection) -> None:
    """Substrate Vault: the ingest scan for instruction-like source content.

    Flagged at ingest, read by the SOURCES pane and by the compile, which wraps
    a flagged source's text in the untrusted-data delimiter
    (``services/compile_guard``). The source still ingests — the user's document
    is the user's document — so the flag is a label, not a gate.
    """
    if not _column_exists(db, "substrate_vault", "instruction_like"):
        db.execute(
            "ALTER TABLE substrate_vault ADD COLUMN instruction_like INTEGER NOT NULL DEFAULT 0"
        )
    if not _column_exists(db, "substrate_vault", "instruction_hits"):
        db.execute(
            "ALTER TABLE substrate_vault ADD COLUMN instruction_hits TEXT NOT NULL DEFAULT '[]'"
        )


def _migrate_v24(db: sqlite3.Connection) -> None:
    """Red-Hat multi-pass audit live telemetry for founder drawer polling."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS redhat_audit_telemetry (
            project_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL DEFAULT '',
            run_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            pass1_complete INTEGER NOT NULL DEFAULT 0,
            pass2_running INTEGER NOT NULL DEFAULT 0,
            findings_json TEXT NOT NULL DEFAULT '[]',
            error TEXT NOT NULL DEFAULT '',
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _migrate_v23(db: sqlite3.Connection) -> None:
    """Multi-pass Red-Hat — block hash cache and audit concurrency locks."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS redhat_cache (
            block_hash TEXT PRIMARY KEY,
            findings_json TEXT NOT NULL,
            pass1_model TEXT NOT NULL DEFAULT '',
            pass2_model TEXT NOT NULL DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_redhat_cache_hash ON redhat_cache(block_hash)")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS redhat_audit_locks (
            project_id TEXT PRIMARY KEY,
            active_task_id TEXT NOT NULL DEFAULT '',
            generation INTEGER NOT NULL DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _migrate_v22(db: sqlite3.Connection) -> None:
    """Founder workbench Phase 2 — Red-Hat findings on runs."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS redhat_findings (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'medium',
            suggested_fix TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            dismissal_rationale TEXT NOT NULL DEFAULT '',
            model_used TEXT NOT NULL DEFAULT '',
            highlight_text TEXT NOT NULL DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_redhat_findings_run ON redhat_findings(run_id, created_at DESC)"
    )


def _migrate_v21(db: sqlite3.Connection) -> None:
    """Founder workbench Phase 1 — runs and drafts."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            directive TEXT NOT NULL,
            content TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT 'gemini',
            sources_used TEXT NOT NULL DEFAULT '[]',
            extracted_locks TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'draft',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (workspace_id) REFERENCES projects(id) ON DELETE SET NULL
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_runs_workspace ON runs(workspace_id, created_at DESC)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS drafts (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL UNIQUE,
            content TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (workspace_id) REFERENCES projects(id) ON DELETE CASCADE
        )
        """
    )


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
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
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
            UNIQUE (project_id, node_id, version),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
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
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
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


_MIGRATED: set[tuple[str, str]] = set()
_MIGRATED_LOCK = threading.Lock()


def _migration_key(conn: sqlite3.Connection | None = None) -> tuple[str, str]:
    """(database, schema) the migration will run in — the connection's own
    schema when one is given, else the schema `_new_connection` would open
    (`history.DB_PATH`). Reading the env alone was wrong: tests point
    `history.DB_PATH` at a fresh path without touching the env, and the cache
    then answered "migrated" for a schema that had no tables (2026-09-24)."""
    try:
        from .pg_compat import current_schema, database_url
    except ImportError:
        from pg_compat import current_schema, database_url
    schema = getattr(conn, "_schema", None) if conn is not None else None
    if not schema:
        try:
            from ..history import DB_PATH as _db_path
        except ImportError:
            from history import DB_PATH as _db_path
        schema = current_schema(str(_db_path))
    return (database_url() or "", str(schema))


def reset_init_db_cache() -> None:
    """Forget which (database, schema) pairs this process has migrated."""
    with _MIGRATED_LOCK:
        _MIGRATED.clear()


def init_db(conn: sqlite3.Connection | None = None) -> None:
    """Create or migrate tables on startup. Safe to call repeatedly.

    Fast path: once a (database, schema) pair has been migrated by this process
    the call returns without touching the database. Nearly every repository
    function starts with ``init_db()``, and before this each call replayed the
    whole migration script — measured 37 ms and 61 round trips per call, ~110 ms
    for one ingest-jobs poll, and every ``CREATE TABLE IF NOT EXISTS`` also
    flushed pg_compat's catalog caches (audit 2026-09-24). Tests that switch
    ``DATABASE_PATH`` get a new schema and therefore a new key;
    ``ASSURE_FORCE_MIGRATE=1`` disables the cache.

    A migration that fails half way used to leave its DDL uncommitted on whatever
    connection it was given — the request-scoped one, for the callers that reach it
    through `get_db()`. Both damages that follow are the family's: the connection
    holds SQLite's write lock while the failed migration waits for a commit that
    will never come, and whoever commits that connection next — the request's own
    write — commits the partial migration along with it. Rolled back here, on the
    failure path, before the error travels.

    Given no connection, it takes one from `db_scope()`: this is called by nearly
    every repository function, so an unscoped `get_db()` here was the single
    largest source of the unreturned checkouts db_scope measures.
    """
    key = _migration_key(conn)
    force = os.environ.get("ASSURE_FORCE_MIGRATE", "").strip().lower() in ("1", "true", "yes")
    if not force and key in _MIGRATED:
        return
    if conn is not None:
        _migrations_guarded(conn)
    else:
        with db_scope() as db:
            _migrations_guarded(db)
    with _MIGRATED_LOCK:
        _MIGRATED.add(key)


_MIGRATION_LOCK_KEY = 727001  # arbitrary, constant: one lock for this app's schema


def _migrations_guarded(db: sqlite3.Connection) -> None:
    """Run the migrations, rolling back whatever a failure left pending.

    Serialised with a PostgreSQL advisory lock: gunicorn starts several workers
    at once and the Celery worker boots alongside, and each calls init_db() on
    import. Two of them running `CREATE TABLE … IF NOT EXISTS` / `CREATE OR
    REPLACE FUNCTION` against an empty database raced, and one gunicorn worker
    died at start-up with an exception in create_app (EC2 first boot,
    2026-09-24; the arbiter respawned it, so the symptom was only a traceback
    in the log). The lock is session-level on this connection and released in
    `finally`; a second process simply waits and then finds nothing to do.
    """
    locked = False
    try:
        try:
            db.execute("SELECT pg_advisory_lock(?)", (_MIGRATION_LOCK_KEY,))
            locked = True
        except Exception:
            locked = False  # not PostgreSQL (tests with a shim) — run unguarded
        _migrate_db(db)
    except BaseException:
        _rollback_quietly(db)
        raise
    finally:
        if locked:
            try:
                db.execute("SELECT pg_advisory_unlock(?)", (_MIGRATION_LOCK_KEY,))
                db.commit()
            except Exception:
                pass


def _migrate_db(db: sqlite3.Connection) -> None:
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
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
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
            meta TEXT,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS jdf_documents (
            project_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            tree_json TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
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
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
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
    if current < 21:
        _migrate_v21(db)
    if current < 22:
        _migrate_v22(db)
    if current < 23:
        _migrate_v23(db)
    if current < 24:
        _migrate_v24(db)
    if current < 25:
        _migrate_v25(db)
    if current < 26:
        _migrate_v26(db)
    if current < 27:
        _migrate_v27(db)
    if current < 28:
        _migrate_v28(db)
    if current < 29:
        _migrate_v29(db)
    if current < 30:
        _migrate_v30(db)
    if current < 31:
        _migrate_v31(db)
    if current < 32:
        _migrate_v32(db)

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
        except BaseException as exc:
            # Every failure here abandons the connection that was just opened — a
            # retry opens another one — so it is released before either the retry
            # (a lock, worth another attempt) or the raise (anything else, but a
            # DatabaseError from a pragma reaches here too) is decided.
            _release_direct_connection(conn)
            if not isinstance(exc, sqlite3.OperationalError):
                raise
            last_exc = exc
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


@contextmanager
def closing_connection(
    path: str | Path | None = None,
    *,
    timeout: float = 5.0,
    site: str = "db.connection.closing_connection",
) -> Iterator[sqlite3.Connection]:
    """A direct connection that is rolled back and closed on every path.

    For the callers that need a connection *of their own* rather than the
    request-scoped `get_db()` handle: the audit trail, the metrics collector, the
    /health probes, the compliance export. They open their connection, do their
    work and close it inside one `try`, and their handler swallows the failure —
    because a metrics sample or an audit row is best-effort by design. What that
    shape hid is the connection itself: when anything between the connect and the
    close raised, the connection stayed open, holding the write lock of whatever
    statement had just failed, and the next writer — a different connection
    entirely — waited on that lock and timed out. That is the family.

    The failure policy stays with the caller; the resource does not. Rolled back
    before the close so the lock goes with it, and closed in a `finally` so the
    policy cannot outlive the block.
    """
    conn = _new_connection()
    note_connection_open(conn, site=site)
    try:
        yield conn
    finally:
        _release_direct_connection(conn)


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
