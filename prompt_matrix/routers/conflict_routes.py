"""GET /api/projects/<id>/conflicts — keyword-level vault disagreements."""

from __future__ import annotations

from flask import jsonify, request

try:
    from ..db.jdf_repository import fetch_latest_jdf_or_empty
    from ..ledger.source_conflicts import conflicts_for_project, detect_source_conflicts
    from ..middleware import project_ownership_required
except ImportError:
    from db.jdf_repository import fetch_latest_jdf_or_empty
    from ledger.source_conflicts import conflicts_for_project, detect_source_conflicts
    from middleware import project_ownership_required


def register_conflict_routes(app) -> None:
    @app.get("/api/projects/<project_id>/conflicts")
    @project_ownership_required
    def list_project_conflicts(project_id: str):
        refresh = (request.args.get("refresh") or "1").strip().lower() not in {"0", "false", "no"}
        if refresh:
            doc = fetch_latest_jdf_or_empty(project_id)
            rows = detect_source_conflicts(project_id, doc)
        else:
            rows = conflicts_for_project(project_id)
        return jsonify(
            {
                "ok": True,
                "conflicts": rows,
                "count": len(rows),
                "implementation": "keyword-level",
                "limitation": (
                    "Naive numeric/date/term matching across included vault files. "
                    "Semantic contradiction detection is deferred to v2.0."
                ),
            }
        )
