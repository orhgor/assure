"""Celery tasks for async Substrate Vault ingestion."""

from __future__ import annotations

from typing import Any

from prompt_matrix.celery_app import celery_app


@celery_app.task(name="assure.process_substrate_upload", bind=True, queue="sqlite_writes")
def process_substrate_upload(
    self,
    project_id: str,
    file_path: str,
    original_filename: str,
) -> dict[str, Any]:
    """Extract text from a temp upload and persist to the vault."""
    from pathlib import Path

    path = Path(file_path)
    try:
        from prompt_matrix.routers.substrate import ingest_substrate_file

        file_bytes = path.read_bytes()
        entry = ingest_substrate_file(project_id, original_filename, file_bytes)
        return {
            "status": "success",
            "task_id": self.request.id,
            "entry": entry,
        }
    except Exception as exc:
        return {
            "status": "failure",
            "task_id": self.request.id,
            "error": str(exc),
        }
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
