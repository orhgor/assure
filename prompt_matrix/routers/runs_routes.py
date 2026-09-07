"""Founder workbench runs API."""

from __future__ import annotations

from flask import jsonify, request
from pydantic import BaseModel, ConfigDict, Field

try:
    from ..db.runs_repository import delete_run, fetch_run, list_runs
    from ..services.macro_verify import contradictions_for_run
    from ..services.run_creation import create_run_from_directive
except ImportError:
    from db.runs_repository import delete_run, fetch_run, list_runs
    from services.macro_verify import contradictions_for_run
    from services.run_creation import create_run_from_directive


class RunCreatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    directive: str = ""
    workspace_id: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    model: str = "gemini"


def register_runs_routes(app) -> None:
    @app.post("/api/runs")
    def create_run():
        try:
            payload = RunCreatePayload.model_validate(request.get_json(silent=True) or {})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if not payload.directive.strip():
            return jsonify({"ok": False, "error": "directive is required"}), 400
        try:
            run = create_run_from_directive(
                payload.directive,
                workspace_id=payload.workspace_id,
                source_ids=payload.source_ids,
                model=payload.model,
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({"ok": True, "run": run}), 201

    @app.get("/api/runs")
    def get_runs():
        workspace_id = request.args.get("workspace_id")
        runs = list_runs(workspace_id=workspace_id)
        return jsonify({"ok": True, "runs": runs, "count": len(runs)})

    @app.get("/api/runs/<run_id>")
    def get_run(run_id: str):
        run = fetch_run(run_id)
        if not run:
            return jsonify({"ok": False, "error": "run not found"}), 404
        return jsonify({"ok": True, "run": run})

    @app.delete("/api/runs/<run_id>")
    def remove_run(run_id: str):
        if not delete_run(run_id):
            return jsonify({"ok": False, "error": "run not found"}), 404
        return jsonify({"ok": True})

    @app.get("/api/runs/<run_id>/contradictions")
    def run_contradictions(run_id: str):
        run = fetch_run(run_id)
        if not run:
            return jsonify({"ok": False, "error": "run not found"}), 404
        compare = request.args.getlist("run_ids") or request.args.get("run_ids", "")
        compare_ids: list[str] = []
        if isinstance(compare, str) and compare.strip():
            compare_ids = [x.strip() for x in compare.split(",") if x.strip()]
        elif isinstance(compare, list):
            compare_ids = compare
        workspace_id = request.args.get("workspace_id") or run.get("workspace_id")
        conflicts = contradictions_for_run(
            run_id,
            workspace_id=workspace_id,
            compare_run_ids=compare_ids or None,
        )
        return jsonify({"ok": True, "conflicts": conflicts, "count": len(conflicts)})
