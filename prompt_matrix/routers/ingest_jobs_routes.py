"""Ingest job tracking: watch a document move through parse → verify → persist.

Reads ``ingest_jobs`` (db/ingest_jobs_repository). The workbench's processing
panel polls the list; the report endpoint joins the revision the job produced
so the Z3 verdict is shown with its actual violations, not just a status word.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from flask import jsonify, request

try:
    from ..db.ingest_jobs_repository import ACTIVE_STATUSES, get_job, job_stats, list_jobs
    from ..middleware import project_ownership_required
except ImportError:
    from db.ingest_jobs_repository import ACTIVE_STATUSES, get_job, job_stats, list_jobs
    from middleware import project_ownership_required

log = logging.getLogger(__name__)


def _revision_verification(project_id: str, revision_id: str | None) -> dict[str, Any] | None:
    """The stored tree's Z3 result (``meta["z3"]``) and Red-Hat finding count."""
    if not revision_id:
        return None
    try:
        from ..history import get_db
    except ImportError:
        from history import get_db
    row = get_db().execute(
        "SELECT jdf_tree, version FROM jdf_revisions WHERE id = ? AND project_id = ?",
        (revision_id, project_id),
    ).fetchone()
    if not row:
        return None
    try:
        tree = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    z3 = (tree.get("meta") or {}).get("z3") if isinstance(tree.get("meta"), dict) else None
    findings = 0
    nodes = 0
    stack = list(tree.get("body") or [])
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        nodes += 1
        annotations = node.get("annotations") or {}
        redhat = annotations.get("redhat") if isinstance(annotations, dict) else None
        if isinstance(redhat, list):
            findings += len(redhat)
        stack.extend(node.get("children") or [])
    return {
        "revision_id": revision_id,
        "version": row[1],
        "node_count": nodes,
        "z3": z3,
        "redhat_finding_count": findings,
    }


def register_ingest_jobs_routes(app) -> None:
    @app.get("/api/projects/<project_id>/ingest-jobs")
    @project_ownership_required
    def list_ingest_jobs(project_id: str):
        status = (request.args.get("status") or "").strip() or None
        try:
            limit = int(request.args.get("limit") or 50)
        except ValueError:
            limit = 50
        jobs = list_jobs(project_id, limit=limit, status=status)
        return jsonify(
            {
                "ok": True,
                "jobs": jobs,
                "stats": job_stats(project_id),
                "active": any(j["status"] in ACTIVE_STATUSES for j in jobs),
            }
        )

    @app.get("/api/projects/<project_id>/ingest-jobs/<job_id>")
    @project_ownership_required
    def get_ingest_job(project_id: str, job_id: str):
        job = get_job(job_id)
        if not job or job["project_id"] != project_id:
            return jsonify({"ok": False, "error": "Ingest job not found."}), 404
        return jsonify({"ok": True, "job": job})

    @app.get("/api/projects/<project_id>/ingest-jobs/<job_id>/report")
    @project_ownership_required
    def ingest_job_report(project_id: str, job_id: str):
        """The job plus the verification detail behind its Z3 verdict.

        ``z3.violations`` are the contradictions the truth ledger found on the
        stored revision; an empty list with ``z3_status: PASS`` means the check
        ran and found none, ``TIMEOUT``/``ERROR`` mean it did not complete and
        the document is unverified — never rendered as a pass.
        """
        job = get_job(job_id)
        if not job or job["project_id"] != project_id:
            return jsonify({"ok": False, "error": "Ingest job not found."}), 404
        verification = _revision_verification(project_id, job.get("revision_id"))
        omp = None
        if job.get("omp_artifact_id"):
            try:
                try:
                    from ..services.omp import load_omp_artifact
                except ImportError:
                    from services.omp import load_omp_artifact
                artifact = load_omp_artifact(job["omp_artifact_id"])
                if artifact is not None:
                    omp = {
                        "artifact_id": artifact.artifact_id,
                        "artifact_type": artifact.artifact_type,
                        "confidence": artifact.confidence,
                        "provenance": artifact.provenance,
                        "created_at": artifact.created_at,
                    }
            except Exception:
                log.exception("ingest job report: OMP artifact load failed")
        return jsonify({"ok": True, "job": job, "verification": verification, "omp": omp})

    @app.post("/api/projects/<project_id>/ingest-jobs/<job_id>/retry")
    @project_ownership_required
    def retry_ingest_job(project_id: str, job_id: str):
        """Re-queue a failed job whose staged object still exists.

        The object is deleted only after a successful parse, so a failed job
        can be retried as long as the bucket lifecycle (1 day) has not expired
        it; otherwise the answer is 409 and the user re-uploads.
        """
        job = get_job(job_id)
        if not job or job["project_id"] != project_id:
            return jsonify({"ok": False, "error": "Ingest job not found."}), 404
        if job["status"] not in ("failed", "skipped"):
            return jsonify({"ok": False, "error": f"Job is {job['status']}; only failed jobs can be retried."}), 409
        try:
            from ..db.ingest_jobs_repository import advance, set_task
            from ..services.object_store import get_object_store
        except ImportError:
            from db.ingest_jobs_repository import advance, set_task
            from services.object_store import get_object_store
        store = get_object_store()
        if not job.get("object_key") or not store.exists(job["object_key"]):
            return jsonify({"ok": False, "error": "The uploaded file is no longer staged; upload it again."}), 409
        if job["kind"] == "substrate_upload":
            try:
                from ..tasks.substrate_tasks import process_substrate_upload as task_fn
            except ImportError:
                from tasks.substrate_tasks import process_substrate_upload as task_fn
        else:
            try:
                from ..tasks.parse_tasks import import_project_pdf_task as task_fn
            except ImportError:
                from tasks.parse_tasks import import_project_pdf_task as task_fn
        advance(job_id, "queued", error=None)
        task = task_fn.apply_async(args=[project_id, job["object_key"], job["filename"], job_id])
        set_task(job_id, task.id)
        return jsonify({"ok": True, "job_id": job_id, "task_id": task.id, "status": "queued"}), 202
