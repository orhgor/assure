"""Analytics SQL views (schema v18)."""

from __future__ import annotations

import sqlite3

VIEW_DDL = [
    """
    CREATE VIEW IF NOT EXISTS view_z3_health AS
    SELECT
        DATE(created_at) AS audit_date,
        COUNT(*) AS total_checks,
        SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS passed_locks,
        SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failed_locks,
        ROUND(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS pass_rate_pct
    FROM audit_log
    WHERE action IN ('Z3_VIOLATION', 'DRIFT_CHECK', 'DRAFT_STREAM_REDHAT')
    GROUP BY DATE(created_at)
    """,
    """
    CREATE VIEW IF NOT EXISTS view_redhat_critiques AS
    SELECT
        COALESCE(json_extract(details, '$.issue_type'), action) AS critique_category,
        COUNT(*) AS frequency,
        project_id
    FROM audit_log
    WHERE action LIKE '%REDHAT%'
    GROUP BY critique_category, project_id
    ORDER BY frequency DESC
    """,
    """
    CREATE VIEW IF NOT EXISTS view_compliance_velocity AS
    SELECT
        p.id AS project_id,
        p.title,
        COALESCE(
            (SELECT COUNT(*) FROM sign_offs s WHERE s.project_id = p.id AND s.status = 'approved'),
            0
        ) AS total_sign_offs,
        CASE WHEN dl.id IS NOT NULL THEN 1 ELSE 0 END AS is_locked
    FROM projects p
    LEFT JOIN document_locks dl ON dl.project_id = p.id
    """,
]


def ensure_analytics_views(conn: sqlite3.Connection | None = None) -> None:
    try:
        from ..history import get_db
    except ImportError:
        from history import get_db

    db = conn or get_db()
    for ddl in VIEW_DDL:
        db.execute(ddl)
    db.commit()
