"""Founder workbench runs API."""

from __future__ import annotations

import json

from flask import Response, jsonify, request, stream_with_context
from pydantic import BaseModel, ConfigDict, Field

try:
    from ..db.runs_repository import delete_run, fetch_run, list_runs
    from ..db.redhat_findings_repository import fetch_finding, update_finding_status
    from ..services.auto_compiler import done_sse, run_auto_compiler_pipeline
    from ..services.founder_redhat import get_run_with_findings, run_adversarial_redhat
    from ..services.lock_metadata import enrich_extracted_locks
    from ..services.macro_verify import contradictions_for_run
    from ..services.run_creation import create_run_from_directive
except ImportError:
    from db.runs_repository import delete_run, fetch_run, list_runs
    from db.redhat_findings_repository import fetch_finding, update_finding_status
    from services.auto_compiler import done_sse, run_auto_compiler_pipeline
    from services.founder_redhat import get_run_with_findings, run_adversarial_redhat
    from services.lock_metadata import enrich_extracted_locks
    from services.macro_verify import contradictions_for_run
    from services.run_creation import create_run_from_directive


class RunCreatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    directive: str = ""
    workspace_id: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    model: str = "gemini"
    stream: bool = False


class FindingUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str = ""
    dismissal_rationale: str = ""
    suggested_fix: str = ""


def _wants_sse(payload: RunCreatePayload) -> bool:
    if payload.stream:
        return True
    accept = (request.headers.get("Accept") or "").lower()
    return "text/event-stream" in accept


def register_runs_routes(app) -> None:
    @app.post("/api/runs")
    def create_run():
        try:
            payload = RunCreatePayload.model_validate(request.get_json(silent=True) or {})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if not payload.directive.strip():
            return jsonify({"ok": False, "error": "directive is required"}), 400

        if _wants_sse(payload):

            def generate():
                try:
                    yield from run_auto_compiler_pipeline(
                        payload.directive,
                        workspace_id=payload.workspace_id,
                        source_ids=payload.source_ids,
                        model=payload.model,
                    )
                except Exception as exc:
                    yield (
                        f"event: error\ndata: {json.dumps({'ok': False, 'error': str(exc)})}\n\n"
                    )
                    yield done_sse()

            headers = {
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
            return Response(stream_with_context(generate()), headers=headers)

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
        include_findings = request.args.get("include_findings") == "1"
        if include_findings:
            enriched = []
            for run in runs:
                full = get_run_with_findings(run["id"])
                enriched.append(full or run)
            runs = enriched
        return jsonify({"ok": True, "runs": runs, "count": len(runs)})

    @app.get("/api/runs/<run_id>")
    def get_run(run_id: str):
        run = get_run_with_findings(run_id)
        if not run:
            return jsonify({"ok": False, "error": "run not found"}), 404
        return jsonify({"ok": True, "run": run})

    @app.post("/api/runs/<run_id>/redhat")
    def run_redhat(run_id: str):
        run = fetch_run(run_id)
        if not run:
            return jsonify({"ok": False, "error": "run not found"}), 404
        try:
            findings = run_adversarial_redhat(
                run_id,
                workspace_id=run.get("workspace_id"),
            )
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({"ok": True, "findings": findings, "count": len(findings)})

    @app.patch("/api/runs/<run_id>/findings/<finding_id>")
    def update_finding(run_id: str, finding_id: str):
        finding = fetch_finding(finding_id)
        if not finding or finding.get("run_id") != run_id:
            return jsonify({"ok": False, "error": "finding not found"}), 404
        try:
            payload = FindingUpdatePayload.model_validate(request.get_json(silent=True) or {})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        action = payload.action.strip().lower()
        if action == "accept":
            updated = update_finding_status(
                finding_id,
                status="accepted",
                suggested_fix=payload.suggested_fix or finding.get("suggested_fix") or "",
            )
        elif action == "dismiss":
            if not payload.dismissal_rationale.strip():
                return jsonify({"ok": False, "error": "dismissal rationale required"}), 400
            updated = update_finding_status(
                finding_id,
                status="dismissed",
                dismissal_rationale=payload.dismissal_rationale.strip(),
            )
        else:
            return jsonify({"ok": False, "error": "action must be accept or dismiss"}), 400
        return jsonify({"ok": True, "finding": updated})

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
