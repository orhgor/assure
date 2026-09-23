"""Celery tasks for the parse tier: documents are parsed on workers, never in a request.

The web replica stages the upload in the object store and enqueues the key;
the worker fetches the bytes, runs the same pipeline the synchronous route
runs (``services/pdf_ingest``), and deletes the staged object. Redelivery
after a crash is safe: a key that is already gone means the previous attempt
finished, and the task reports that instead of parsing twice.
"""

from __future__ import annotations

import logging
from typing import Any

from prompt_matrix.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(
    name="assure.import_project_pdf",
    bind=True,
    queue="parse",
    acks_late=True,
    soft_time_limit=int(__import__("os").environ.get("PARSE_TASK_SOFT_LIMIT", "840")),
)
def import_project_pdf_task(
    self, project_id: str, object_key: str, filename: str, job_id: str | None = None
) -> dict[str, Any]:
    """Parse one staged PDF into the project (JDF CI / OCR / Textract → verify → revision → OMP).

    ``job_id`` names the ``ingest_jobs`` row the web tier created; every stage
    is recorded there so the user can watch the document move even when the
    Celery result has expired.
    """
    from prompt_matrix.db.ingest_jobs_repository import advance
    from prompt_matrix.services.object_store import get_object_store
    from prompt_matrix.services.pdf_ingest import PdfIngestError, ingest_pdf_for_project

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
        file_bytes = store.get_bytes(object_key)
        payload = ingest_pdf_for_project(project_id, filename, file_bytes, job_id=job_id)
        result = {"status": "success", "task_id": self.request.id, "job_id": job_id, "result": payload}
    except PdfIngestError as exc:
        result = {
            "status": "failure",
            "task_id": self.request.id,
            "job_id": job_id,
            "error": str(exc),
            "http_status": exc.http_status,
        }
    except Exception as exc:  # a bug, not a document problem: keep the object for a retry
        log.exception("import_project_pdf_task failed for %s", object_key)
        if self.request.retries < 1:
            _job("queued", error=f"retrying after {exc.__class__.__name__}")
            raise self.retry(exc=exc, countdown=30)
        _job("failed", error=f"{exc.__class__.__name__}: {str(exc)[:1500]}")
        return {"status": "failure", "task_id": self.request.id, "job_id": job_id, "error": str(exc)}
    store.delete(object_key)
    return result
