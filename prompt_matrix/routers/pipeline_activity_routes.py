"""``GET /api/projects/<id>/pipeline-activity`` — did each stage run, and did
its request leave the server?

Why (customer report, 2026-09-28): "requests are not reaching OpenRouter and I
cannot see whether the pipeline ran" (OpenRouter was the backend then; it was
replaced by Amazon Bedrock on 2026-10-01 and the route is backend-neutral). The stage summaries (the ingest job, the
Parsure ``report.execution`` ledger) say what a stage concluded; the model-call
ledger (``services/model_calls``) says which requests were made, to which model,
with what HTTP status and latency. This route joins the three into one list in
the pipeline's order, plus a live probe of the backend (can a request leave at
all) and the newest calls — the material the workbench's "Pipeline activity"
panel shows. A stage with no evidence is ``no_record``; nothing here is
defaulted to look green.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import jsonify, request

try:
    from ..db import ingest_jobs_repository as jobs_repo
    from ..db import model_calls_repository as calls_repo
    from ..db import parsure_repository
    from ..middleware import project_ownership_required
    from ..rbac import requires
    from ..services import model_calls as mc
except ImportError:  # pragma: no cover - flat-import fallback
    from db import ingest_jobs_repository as jobs_repo  # type: ignore
    from db import model_calls_repository as calls_repo  # type: ignore
    from db import parsure_repository  # type: ignore
    from middleware import project_ownership_required  # type: ignore
    from rbac import requires  # type: ignore
    from services import model_calls as mc  # type: ignore

log = logging.getLogger(__name__)

#: Parsure ``report.execution`` step → pipeline stage, and how its status reads.
_EXECUTION_STEPS = {
    "laya": "intake",
    "z3": "z3",
    "llm_grounding": "llm_grounding",
    "discovery": "discovery",
    "vision": "vision",
    "redhat_graph": "redhat_graph",
    "redhat_targeted": "redhat_targeted",
}
_RAN = {"ran", "completed", "complete", "ok", "done", "pass", "passed", "verified", "promoted", "not_needed"}
_SKIPPED = {"skipped", "not_run", "off", "disabled", "not_applicable", "not_needed_no_fields"}
_FAILED = {"failed", "error", "errored", "timeout", "timed_out", "unavailable"}


def _status_word(value: Any) -> str:
    v = str(value or "").strip().lower()
    if not v:
        return "no_record"
    if v in _FAILED:
        return "failed"
    if v in _SKIPPED:
        return "skipped"
    if v in _RAN:
        return "ran"
    return "ran" if v not in ("pending", "queued", "running") else "pending"


def _execution_detail(step: str, block: Any) -> str | None:
    if not isinstance(block, dict):
        return None
    parts: list[str] = []
    if step == "llm_grounding":
        if block.get("fields_offered") is not None:
            parts.append(f"{block.get('fields_offered')} fields offered, {block.get('fields_grounded', 0)} grounded")
        if block.get("model_path"):
            parts.append(f"model path {block['model_path']}")
    elif step == "discovery":
        parts.append(f"{block.get('pairs', 0)} pairs ({block.get('taxonomy_scan', 0)} schema labels, {block.get('heuristic', 0)} heuristic, {block.get('llm_grounded', 0)} model)")
    elif step == "vision":
        if block.get("pages") is not None:
            parts.append(f"{block.get('pages')} page(s)")
    elif step == "laya":
        parts.append("escalated to human review" if block.get("human_review") else f"policy {block.get('policy', '')}".strip())
    elif step == "z3":
        if block.get("status"):
            parts.append(str(block.get("status")))
        if block.get("violations") is not None:
            parts.append(f"{block.get('violations')} violation(s)")
    elif step == "redhat_graph":
        if block.get("findings") is not None:
            parts.append(f"{block.get('findings')} finding(s)")
    if block.get("reason"):
        parts.append(str(block["reason"]))
    if block.get("error"):
        parts.append(str(block["error"]))
    return " · ".join(p for p in parts if p) or None


def _parse_stage(job: dict[str, Any] | None) -> dict[str, Any]:
    if not job:
        return {"status": "no_record", "detail": None, "last_at": None, "last_ms": None, "last_error": None}
    status = str(job.get("status") or "")
    word = "ran" if status == "done" else "failed" if status == "failed" else "skipped" if status == "skipped" else "pending"
    parts = []
    if job.get("parser_name"):
        parts.append(str(job["parser_name"]))
    if job.get("page_count") is not None:
        parts.append(f"{job['page_count']} page(s)")
    if job.get("ocr_confidence") is not None:
        parts.append(f"OCR {float(job['ocr_confidence']):.2f}")
    if job.get("parse_confidence") is not None:
        parts.append(f"parse {float(job['parse_confidence']):.2f}")
    if job.get("duration_ms") is not None:
        parts.append(f"{float(job['duration_ms']) / 1000.0:.1f} s")
    if word == "pending":
        parts.append(f"stage {status}")
    return {
        "status": word,
        "detail": " · ".join(parts) or None,
        "last_at": job.get("finished_at") or job.get("updated_at") or job.get("created_at"),
        "last_ms": job.get("duration_ms"),
        "last_error": job.get("error"),
        "job_id": job.get("job_id"),
        "filename": job.get("filename"),
    }


def build_activity(project_id: str, *, limit: int = 50, probe: bool = True) -> dict[str, Any]:
    """The payload; shared with tests and any future CLI."""
    latest_job = None
    try:
        jobs = jobs_repo.list_jobs(project_id, limit=1)
        latest_job = jobs[0] if jobs else None
    except Exception as exc:  # noqa: BLE001 — the panel must still render
        log.warning("pipeline-activity: ingest jobs unavailable: %s", exc)
    execution: dict[str, Any] = {}
    report_id = None
    try:
        report = parsure_repository.get_latest_report(project_id)
        if report:
            execution = dict(report.get("execution") or {})
            report_id = report.get("report_id")
    except Exception as exc:  # noqa: BLE001
        log.warning("pipeline-activity: parsure report unavailable: %s", exc)
    rollup: dict[str, dict[str, Any]] = {}
    calls: list[dict[str, Any]] = []
    ledger_error = None
    try:
        rollup = calls_repo.stage_rollup(project_id)
        calls = calls_repo.list_for_project(project_id, limit=limit)
    except Exception as exc:  # noqa: BLE001
        ledger_error = f"{type(exc).__name__}: {exc}"[:200]
        log.warning("pipeline-activity: model_calls unavailable: %s", exc)

    stages: list[dict[str, Any]] = []
    for spec in mc.STAGES:
        name = spec["stage"]
        row: dict[str, Any] = {
            "stage": name, "label": spec["label"], "status": "no_record", "detail": None, "model": None,
            "calls": 0, "ok": 0, "failed": 0, "last_at": None, "last_ms": None, "last_http_status": None,
            "last_error": None, "source": spec["source"],
        }
        if name == "parse":
            row.update(_parse_stage(latest_job))
        exec_step = next((k for k, v in _EXECUTION_STEPS.items() if v == name), None)
        if exec_step and exec_step in execution:
            block = execution.get(exec_step)
            word = _status_word(block.get("status") if isinstance(block, dict) else block)
            row["status"] = word
            row["detail"] = _execution_detail(exec_step, block)
            if isinstance(block, dict):
                row["model"] = block.get("model") or row["model"]
                row["last_at"] = execution.get("ran_at") or row["last_at"]
                if block.get("ms") is not None:
                    row["last_ms"] = block.get("ms")
                if block.get("error") or (word == "failed" and block.get("reason")):
                    row["last_error"] = block.get("error") or block.get("reason")
        agg = rollup.get(name)
        if agg:
            row.update({
                "model": agg["model"] or row["model"], "calls": agg["calls"], "ok": agg["ok"], "failed": agg["failed"],
                "last_at": agg["last_at"] or row["last_at"], "last_ms": agg["last_ms"] if agg["last_ms"] is not None else row["last_ms"],
                "last_http_status": agg["last_http_status"], "last_error": agg["last_error"] or row["last_error"],
            })
            if row["status"] in ("no_record", "skipped"):
                row["status"] = "failed" if agg["ok"] == 0 and agg["failed"] > 0 else "ran"
            elif row["status"] == "ran" and agg["ok"] == 0 and agg["failed"] > 0:
                row["status"] = "failed"
        stages.append(row)

    unknown = rollup.get("unknown")
    payload = {
        "ok": True,
        "project_id": project_id,
        "llm": mc.llm_status(probe=probe, timeout_s=3.0),
        "stages": stages,
        "calls": calls,
        "unattributed_calls": unknown["calls"] if unknown else 0,
        "sources": {"ingest_job": latest_job.get("job_id") if latest_job else None, "parsure_report": report_id,
                    "execution_ran_at": execution.get("ran_at"), "ledger_error": ledger_error},
    }
    return payload


def register_pipeline_activity_routes(app) -> None:
    @app.get("/api/projects/<project_id>/pipeline-activity")
    @requires("projects.read")
    @project_ownership_required
    def pipeline_activity(project_id: str):
        try:
            limit = max(1, min(int(request.args.get("limit", 50)), 500))
        except (TypeError, ValueError):
            limit = 50
        probe = request.args.get("probe", "1").strip().lower() not in ("0", "false", "no")
        return jsonify(build_activity(project_id, limit=limit, probe=probe))

    @app.get("/api/model-calls")
    @requires("audit.read")
    def recent_model_calls():
        """Every project's newest calls — the auditor's view of what left the box."""
        try:
            limit = max(1, min(int(request.args.get("limit", 100)), 1000))
        except (TypeError, ValueError):
            limit = 100
        try:
            calls = calls_repo.list_recent(limit=limit)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": f"model_calls unavailable: {type(exc).__name__}"}), 503
        return jsonify({"ok": True, "calls": calls, "llm": mc.llm_status(probe=False)})
