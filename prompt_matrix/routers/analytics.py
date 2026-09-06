"""Analytics dashboard API."""

from __future__ import annotations

from flask import jsonify

try:
    from ..db.analytics_views import ensure_analytics_views
    from ..history import get_db
except ImportError:
    from db.analytics_views import ensure_analytics_views
    from history import get_db


def register_analytics_routes(app, page_renderer=None) -> None:
    @app.get("/api/analytics/z3-health")
    def analytics_z3_health():
        ensure_analytics_views()
        db = get_db()
        rows = db.execute(
            """
            SELECT audit_date, total_checks, passed_locks, failed_locks, pass_rate_pct
            FROM view_z3_health
            ORDER BY audit_date DESC
            LIMIT 90
            """
        ).fetchall()
        return jsonify(
            {
                "ok": True,
                "rows": [dict(r) for r in rows],
            }
        )

    @app.get("/api/analytics/redhat-critiques")
    def analytics_redhat_critiques():
        ensure_analytics_views()
        db = get_db()
        rows = db.execute(
            """
            SELECT critique_category, frequency, project_id
            FROM view_redhat_critiques
            LIMIT 200
            """
        ).fetchall()
        return jsonify({"ok": True, "rows": [dict(r) for r in rows]})

    @app.get("/api/analytics/compliance-velocity")
    def analytics_compliance_velocity():
        ensure_analytics_views()
        db = get_db()
        rows = db.execute(
            """
            SELECT project_id, title, total_sign_offs, is_locked
            FROM view_compliance_velocity
            ORDER BY total_sign_offs DESC
            LIMIT 100
            """
        ).fetchall()
        return jsonify({"ok": True, "rows": [dict(r) for r in rows]})

    @app.get("/analytics")
    def analytics_page():
        if page_renderer is not None:
            return page_renderer("analytics.html", "analytics")
        from flask import render_template

        return render_template("analytics.html")
