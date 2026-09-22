"""The PDF import pipeline, callable from a request or from a worker.

``POST /api/projects/<id>/import-pdf`` used to hold this whole sequence inline:
route the parser, run JDF CI (or OCR / Textract), build the tree, verify (Z3 +
Red-Hat), save the revision, stage the OMP artifact. Every step is CPU or
network bound and none of it belongs on a web replica's request thread, so the
sequence lives here and has two callers with identical results:

- the route, when ``PARSE_ASYNC`` is off (single-process development), and
- ``tasks/parse_tasks.import_project_pdf_task`` on a worker, when it is on.

The function is deterministic for a given (project, filename, bytes) apart from
the revision number it allocates, and it never touches the request object.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

log = logging.getLogger(__name__)


class PdfIngestError(Exception):
    """A failure the caller reports to the client with ``http_status``."""

    def __init__(self, message: str, *, http_status: int = 400) -> None:
        super().__init__(message)
        self.http_status = http_status


def parse_async_enabled() -> bool:
    """Whether uploads are queued to a worker instead of parsed in the request.

    On by default: the request path must stay short behind a load balancer.
    Turned off only where there is no worker (``PARSE_ASYNC=0``) or when Celery
    is disabled outright, in which case the same pipeline runs inline.
    """
    raw = os.environ.get("PARSE_ASYNC", "").strip().lower()
    if raw in ("0", "false", "no"):
        return False
    if raw in ("1", "true", "yes"):
        # Explicitly on: also under eager Celery, where apply_async runs the
        # task inline and the 202 + task_id contract is still exercised.
        return True
    try:
        from ..celery_app import celery_broker_disabled
    except ImportError:
        from celery_app import celery_broker_disabled
    return not celery_broker_disabled()


def _job_advance(job_id: str | None, stage: str, **fields: Any) -> None:
    """Record a pipeline stage on the ingest job, when there is one.

    Tracking is observability, not control flow: a failure to write the job
    row is logged and the parse continues. The document is the deliverable;
    the row is how the user watches it arrive.
    """
    if not job_id:
        return
    try:
        try:
            from ..db.ingest_jobs_repository import advance
        except ImportError:
            from db.ingest_jobs_repository import advance
        advance(job_id, stage, **fields)
    except Exception:
        log.exception("ingest job %s: could not record stage %s", job_id, stage)


def ingest_pdf_for_project(
    project_id: str, filename: str, file_bytes: bytes, *, job_id: str | None = None
) -> dict[str, Any]:
    """Parse, verify, persist and stage one PDF for a project.

    Returns the payload the import route answers with (revision fields plus
    parser metadata and the OMP artifact id). Raises :class:`PdfIngestError`
    for a failure the client should see; anything else is a bug. With
    ``job_id`` every stage transition and every measured fact (parser, pages,
    OCR confidence, Z3 verdict, revision, artifact, timings) is written to the
    ``ingest_jobs`` row as it happens.
    """
    try:
        from ..db.jdf_repository import save_jdf_revision
        from ..lib.logger import get_audit_logger
        from ..lib.sanitize import sanitize_jdf_node
        from ..models.jdf import parse_document
        from ..routers.jdf_routes import _textract_parse_bundle
        from ..services.jdf_converter import (
            JdfConversionError,
            jdf_to_document_tree,
            ocr_engine,
            pdf_to_parse_bundle,
        )
        from ..services.omp import build_omp_artifact_from_parse, store_omp_artifact
        from ..services.parser_router import select_parser
        from ..services.pdf_import import pdf_bytes_to_jdf
        from ..services.verification import run_verification_after_parse
    except ImportError:
        from db.jdf_repository import save_jdf_revision
        from lib.logger import get_audit_logger
        from lib.sanitize import sanitize_jdf_node
        from models.jdf import parse_document
        from routers.jdf_routes import _textract_parse_bundle
        from services.jdf_converter import (
            JdfConversionError,
            jdf_to_document_tree,
            ocr_engine,
            pdf_to_parse_bundle,
        )
        from services.omp import build_omp_artifact_from_parse, store_omp_artifact
        from services.parser_router import select_parser
        from services.pdf_import import pdf_bytes_to_jdf
        from services.verification import run_verification_after_parse

    import uuid

    request_id = str(uuid.uuid4())
    start_time = time.perf_counter()
    audit = get_audit_logger()
    filename = (filename or "upload.pdf").strip()

    try:
        # Parser *selection* is the router's call (services/parser_router):
        # JDF CI for a text-layer PDF, JDF CI + OCR for a scan, Textract only
        # when configured or when the OCR parse fails. This function executes
        # the decision — the fallbacks below are execution, not a second router.
        _parser = select_parser(file_bytes, filename=filename)
        _job_advance(job_id, "parsing", parser_name=_parser, size_bytes=len(file_bytes))
        if _parser == "textract":
            bundle = _textract_parse_bundle(file_bytes, filename)
        elif _parser == "jdf-ocr":
            try:
                bundle = pdf_to_parse_bundle(
                    file_bytes,
                    strategy="section",
                    filename=filename,
                    source_kind="scanned",
                    ocr=ocr_engine(),
                )
            except JdfConversionError as ocr_exc:
                log.warning(
                    "JDF OCR parse failed for %s, falling back to Textract: %s", filename, ocr_exc
                )
                bundle = _textract_parse_bundle(file_bytes, filename)
            else:
                if not str(bundle.get("text") or "").strip():
                    # OCR ran and read nothing: a failed free attempt, so the
                    # paid path gets the document — and when Textract is not
                    # there either, the answer is the OCR result, not a
                    # Textract configuration error (mirrors routers/substrate).
                    log.warning("JDF OCR read no text from %s; trying Textract", filename)
                    try:
                        bundle = _textract_parse_bundle(file_bytes, filename)
                    except Exception as textract_exc:
                        raise PdfIngestError(
                            "Could not extract enough readable text from this file "
                            "(0 characters after OCR). Upload a clearer scan or a file "
                            "with more visible text.",
                            http_status=400,
                        ) from textract_exc
        else:
            bundle = pdf_to_parse_bundle(
                file_bytes, strategy="section", filename=filename, source_kind="pdf"
            )
        parse_meta = {
            "parser_name": bundle["parser_name"],
            "source_kind": bundle["source_kind"],
            "page_count": bundle["page_count"],
            "parse_confidence": bundle["parse_confidence"],
            "ocr_confidence": bundle["ocr_confidence"],
            "table_count": bundle["table_count"],
            "image_count": bundle["image_count"],
            "figure_count": bundle["figure_count"],
            "asset_summary": bundle["asset_summary"],
        }
        tree = jdf_to_document_tree(
            bundle["jdf"],
            bundle["chunks"],
            document_id=f"doc-{project_id}",
            title=filename,
            parse_meta=parse_meta,
        )
    except Exception as jdf_exc:
        # JDF CI is the default, but the PyMuPDF importer is a best-effort
        # fallback that still preserves structure (text + images per page) —
        # a missing jdf-cli binary must not kill an import that can be parsed
        # another way.
        log.warning("JDF CI parse failed for %s, falling back to PyMuPDF: %s", filename, jdf_exc)
        tree = sanitize_jdf_node(pdf_bytes_to_jdf(file_bytes, project_id=project_id, filename=filename))
        images = [
            {
                "id": child.get("id"),
                "src": child.get("src"),
                "caption": child.get("caption") or child.get("alt") or "",
                "page": (child.get("meta") or {}).get("page"),
            }
            for section in tree.get("body") or []
            for child in section.get("children") or []
            if child.get("type") == "image"
        ]
        bundle = {
            "jdf": tree,
            "chunks": [],
            "text": "",
            "page_count": len(tree.get("body") or []) or 1,
            "parser_name": "pymupdf",
            "source_kind": "pdf",
            "parse_confidence": None,
            "ocr_confidence": None,
            "tables": [],
            "images": images,
            "figures": [],
            "table_count": 0,
            "image_count": len(images),
            "figure_count": 0,
            "asset_summary": {"tables": 0, "images": len(images), "figures": 0},
        }

    _job_advance(
        job_id,
        "verifying",
        parser_name=bundle["parser_name"],
        source_kind=bundle["source_kind"],
        page_count=bundle["page_count"],
        parse_confidence=bundle["parse_confidence"],
        ocr_confidence=bundle["ocr_confidence"],
    )
    try:
        tree = sanitize_jdf_node(tree)
        parse_document(tree)
        # Shared verification hook: Z3 + Red-Hat run here and only here
        # (services/verification). Never raises by contract; guarded anyway
        # because a revision must not fail on verification.
        bundle["jdf"] = tree
        try:
            verification = run_verification_after_parse(bundle)
        except Exception:
            log.exception("post-parse verification failed; storing parse only")
            verification = None
        z3 = (verification or {}).get("z3") or {}
        _job_advance(
            job_id,
            "persisting",
            z3_status=(verification or {}).get("z3_status"),
            z3_violation_count=len(z3.get("violations") or []) if isinstance(z3, dict) else None,
            redhat_status=(verification or {}).get("redhat_status"),
        )
        result = save_jdf_revision(
            project_id,
            tree,
            mutation_type="PDF_IMPORT",
            change_summary=f"Imported {filename}",
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        audit.log_exception(request_id, project_id, "PDF_IMPORT", exc, duration_ms=duration_ms)
        _job_advance(job_id, "failed", error=str(exc)[:2000], duration_ms=duration_ms)
        raise PdfIngestError(str(exc), http_status=400) from exc

    # Stage the parsed payload into OMP right after the revision is saved.
    # Best-effort: the revision is the durable record.
    omp_artifact_id = None
    try:
        substrate_result = {
            "id": result.get("document_id") or f"doc-{project_id}",
            "filename": filename,
            "page_count": bundle["page_count"],
            "text": bundle["text"],
            "tables": bundle["tables"],
            "images": bundle["images"],
            "figures": bundle["figures"],
            "size_bytes": len(file_bytes),
            "is_image": False,
            "table_count": bundle["table_count"],
            "image_count": bundle["image_count"],
            "figure_count": bundle["figure_count"],
            "asset_summary": bundle["asset_summary"],
        }
        omp_artifact = build_omp_artifact_from_parse(
            project_id,
            substrate_result,
            parse_confidence=bundle["parse_confidence"],
            ocr_confidence=bundle["ocr_confidence"],
            parser_name=bundle["parser_name"],
            source_kind=bundle["source_kind"],
            page_count=bundle["page_count"],
            table_count=bundle["table_count"],
            image_count=bundle["image_count"],
            figure_count=bundle["figure_count"],
            asset_summary=bundle["asset_summary"],
            verification=verification,
        )
        store_omp_artifact(project_id, omp_artifact)
        omp_artifact_id = omp_artifact.artifact_id
    except Exception:
        log.exception("PDF import: OMP parse artifact staging failed")

    duration_ms = int((time.perf_counter() - start_time) * 1000)
    _job_advance(
        job_id,
        "done",
        revision_id=result.get("revision_id"),
        revision_version=result.get("version"),
        omp_artifact_id=omp_artifact_id,
        duration_ms=duration_ms,
    )
    audit.log_audit(
        request_id,
        project_id,
        "PDF_IMPORT",
        success=True,
        duration_ms=duration_ms,
        details={
            "filename": filename,
            "version": result.get("version"),
            "parser_name": bundle["parser_name"],
            "page_count": bundle["page_count"],
            "duration_ms": duration_ms,
        },
    )
    return {
        **result,
        "ok": True,
        "parser_name": bundle["parser_name"],
        "source_kind": bundle["source_kind"],
        "page_count": bundle["page_count"],
        "parse_confidence": bundle["parse_confidence"],
        "ocr_confidence": bundle["ocr_confidence"],
        "table_count": bundle["table_count"],
        "image_count": bundle["image_count"],
        "figure_count": bundle["figure_count"],
        "omp_artifact_id": omp_artifact_id,
        "duration_ms": duration_ms,
        "job_id": job_id,
        "z3_status": (verification or {}).get("z3_status"),
        "redhat_status": (verification or {}).get("redhat_status"),
    }
