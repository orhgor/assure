"""Celery tasks for async Substrate Vault ingestion."""

from __future__ import annotations

import logging
from typing import Any

from prompt_matrix.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="assure.process_substrate_upload", bind=True, queue="parse", acks_late=True)
def process_substrate_upload(
    self,
    project_id: str,
    object_key: str,
    original_filename: str,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Extract text from a staged upload (object store key) and persist to the vault.

    The object is deleted once the vault row exists; a redelivery that finds
    it gone reports ``skipped`` instead of ingesting the document twice. The
    ``ingest_jobs`` row (``job_id``) records each stage and the vault row id.
    """
    from prompt_matrix.db.ingest_jobs_repository import advance
    from prompt_matrix.services.object_store import get_object_store

    def _job(stage: str, **fields: Any) -> None:
        if not job_id:
            return
        try:
            advance(job_id, stage, **fields)
        except Exception:
            log.exception("ingest job %s: could not record stage %s", job_id, stage)

    store = get_object_store()
    _job("fetching", task_id=self.request.id)
    if not store.exists(object_key):
        _job("skipped", error="staged object not found (already processed or expired)")
        return {
            "status": "skipped",
            "task_id": self.request.id,
            "job_id": job_id,
            "reason": "staged object not found (already processed or expired)",
        }
    try:
        from prompt_matrix.routers.substrate import ingest_substrate_file

        file_bytes = store.get_bytes(object_key)
        _job("parsing", size_bytes=len(file_bytes))
        # ingest_substrate_file records "verifying" / "persisting" (with the Z3
        # verdict) on the job itself; the terminal stage is written here.
        entry = ingest_substrate_file(project_id, original_filename, file_bytes, job_id=job_id)
        z3 = entry.get("verification") or {}
        z3 = z3.get("z3") if isinstance(z3, dict) else None
        _job(
            "done",
            parser_name=entry.get("parser_name"),
            source_kind=entry.get("source_kind"),
            page_count=entry.get("page_count"),
            parse_confidence=entry.get("parse_confidence"),
            ocr_confidence=entry.get("ocr_confidence"),
            substrate_file_id=entry.get("id"),
            omp_artifact_id=entry.get("omp_artifact_id"),
            z3_status=entry.get("z3_status"),
            z3_violation_count=(
                len(z3.get("violations") or []) if isinstance(z3, dict) else None
            ),
            redhat_status=entry.get("redhat_status"),
        )
        result: dict[str, Any] = {"status": "success", "task_id": self.request.id, "job_id": job_id, "entry": entry}
    except Exception as exc:
        _job("failed", error=f"{exc.__class__.__name__}: {str(exc)[:1500]}")
        result = {"status": "failure", "task_id": self.request.id, "job_id": job_id, "error": str(exc)}
    store.delete(object_key)
    return result
