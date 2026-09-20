"""Analytics dashboard API."""

from __future__ import annotations

from flask import jsonify

try:
    from ..cloud_auth import role_required
    from ..db.analytics_views import ensure_analytics_views
    from ..history import get_db
except ImportError:
    from cloud_auth import role_required
    from db.analytics_views import ensure_analytics_views
    from history import get_db


def register_analytics_routes(app, page_renderer=None) -> None:
    # These three views aggregate every project in the workspace — other people's
    # titles, locks and critiques — so they are the admin-only surface.
    @app.get("/api/analytics/z3-health")
    @role_required("admin")
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
    @role_required("admin")
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
    @role_required("admin")
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
