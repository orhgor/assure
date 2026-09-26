"""Substrate Vault upload routes — Textract ingestion (multi-page PDFs supported)."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from pathlib import Path

from flask import jsonify, request
from pydantic import BaseModel, Field
from werkzeug.utils import secure_filename

try:
    from ..db.jdf_repository import ensure_project, fetch_latest_jdf_or_empty
    from ..db.substrate_repository import (
        delete_substrate_entry,
        fetch_substrate_entry,
        list_substrate_for_project,
        save_substrate_entry,
        save_substrate_text,
        set_substrate_included,
        upsert_substrate_entry,
    )
    from ..lib.logger import get_audit_logger
    from ..lib.textract import IMAGE_EXTENSIONS, TextractClient, TextractError
    from ..models.jdf import flatten_nodes
    from ..middleware import project_ownership_required
    from ..services.compile_guard import flag_fields, flag_response
    from ..services.jdf_memory import forget_jdf_document, remember_jdf_document
    from ..services.omp import (
        store_omp_artifact,
        build_omp_artifact_from_parse,
    )
    from ..services.omp_memory import remember_vault_file
    from ..services.verification import run_verification_after_parse
    from ..upload_limits import UploadRejectedError, validate_upload_bytes
except ImportError:
    from db.jdf_repository import ensure_project, fetch_latest_jdf_or_empty
    from db.substrate_repository import (
        delete_substrate_entry,
        fetch_substrate_entry,
        list_substrate_for_project,
        save_substrate_entry,
        save_substrate_text,
        set_substrate_included,
        upsert_substrate_entry,
    )
    from lib.logger import get_audit_logger
    from lib.textract import IMAGE_EXTENSIONS, TextractClient, TextractError
    from models.jdf import flatten_nodes
    from middleware import project_ownership_required
    from services.compile_guard import flag_fields, flag_response
    from services.jdf_memory import forget_jdf_document, remember_jdf_document
    from services.omp import (
        store_omp_artifact,
        build_omp_artifact_from_parse,
    )
    from services.omp_memory import remember_vault_file
    from services.verification import run_verification_after_parse
    from upload_limits import UploadRejectedError, validate_upload_bytes

# Demo config: 200 pages for staging; production default stays at 50.
# The client demo shows real documents (policies, filings) that exceed 50.
TEXTRACT_MAX_PAGES = int(os.environ.get("ASSURE_MAX_PAGES", "50"))

log = logging.getLogger(__name__)

#: A document whose extraction carries no more than this many characters is a scan
#: with no text layer, not a source. The vault upload and the fetched-PDF path
#: (``services/web_retrieval``) refuse at the same floor.
MIN_EXTRACTED_TEXT_CHARS = 10


class SubstrateIngestError(ValueError):
    """Validation/extraction failure with optional response fields."""

    def __init__(
        self,
        message: str,
        *,
        text_chars: int | None = None,
        page_count: int | None = None,
    ) -> None:
        super().__init__(message)
        self.text_chars = text_chars
        self.page_count = page_count


_TEXT_UPLOAD_SUFFIXES = {".txt", ".md"}


def _is_text_upload(filename: str) -> bool:
    """True for vault uploads that carry their own text (no extraction needed)."""
    return Path(filename).suffix.lower() in _TEXT_UPLOAD_SUFFIXES


def _temp_upload_dir() -> Path:
    override = (os.environ.get("TEMP_UPLOAD_DIR") or "").strip()
    if override:
        return Path(override)
    try:
        from ..history import _resolve_db_path
    except ImportError:
        from history import _resolve_db_path
    db_path = str(_resolve_db_path())
    return Path(db_path).parent / "tmp_uploads"


def _substrate_async_enabled() -> bool:
    return os.environ.get("SUBSTRATE_ASYNC_UPLOAD", "").lower() in ("1", "true", "yes")


def extract_document_text(filename: str, file_bytes: bytes) -> dict:
    """Text, tables, forms and page count of a document, by the upload path's own rules.

    Parser *selection* lives in ``services/parser_router.select_parser`` — this
    function executes the decision it returns: a text-like file wraps its
    content directly (no binary parsing, no probing), a clean PDF goes through
    the JDF CI bundle, and a scan (or a JDF failure) falls back best-effort to
    Docling/Textract — that fallback is execution, not a second router. The
    fetched-PDF path (``services/web_retrieval``) calls this too, so a PDF
    fetched from an allowlisted host is read by the same extractor an upload is.

    A JDF CI parse also returns ``jdf`` and ``chunks`` (jdf-cli's own chunking,
    each chunk carrying its page): the search index and the verification hook
    read those instead of re-splitting the text, so a vault upload is indexed
    with the same chunks the dock ingest (``/jdf/ingest``) would produce. Other
    extractors return neither and the caller chunks the text by paragraph.
    """
    from ..services.parser_router import _TEXT_LIKE_EXTENSIONS, select_parser

    use_docling = os.environ.get("USE_DOCLING", "0").lower() in ("1", "true", "yes")
    extracted: dict | None = None
    ocr_empty = False
    page_count = 1
    parser = select_parser(file_bytes, filename)

    def _suffix(filename: str) -> str:
        return Path(filename).suffix.lower().lstrip(".")

    if parser == "jdf" and (
        _is_text_upload(filename) or _suffix(filename) in _TEXT_LIKE_EXTENSIONS
    ):
        # Text-like file, routed here without any binary probe: the content is
        # already its own extracted form — wrap it directly (the "caller wraps
        # text as JDF" branch of the router contract). Textract and Docling
        # only read documents, so skip both rather than fail inside AWS.
        extracted = {
            "text": file_bytes.decode("utf-8", errors="replace"),
            "tables": [],
            "forms": [],
            "page_count": 1,
            "images": [],
            "figures": [],
            "parser_name": None,
            "source_kind": "text",
            "parse_confidence": None,
            "ocr_confidence": None,
            "table_count": 0,
            "image_count": 0,
            "figure_count": 0,
            "asset_summary": {"tables": 0, "images": 0, "figures": 0},
        }
    elif parser == "jdf":
        # JDF CI first — the default PDF parser path. The bundle carries the
        # text, the structured content (tables/images/figures as distinct
        # lists) and the parse/OCR confidence the vault row and the OMP
        # artifact both record. A converter failure falls back best-effort:
        # log it clearly, then let Docling/Textract take the document if it
        # can — never a text-only collapse while structured parsing works.
        from ..services.jdf_converter import JdfConversionError, pdf_to_parse_bundle

        try:
            bundle = pdf_to_parse_bundle(file_bytes, filename=filename, source_kind="pdf")
            extracted = {
                "jdf": bundle.get("jdf"),
                "chunks": bundle.get("chunks") or [],
                "text": bundle["text"],
                "tables": bundle["tables"],
                "images": bundle["images"],
                "figures": bundle["figures"],
                "forms": [],
                "page_count": bundle["page_count"],
                "parser_name": bundle["parser_name"],
                "source_kind": bundle["source_kind"],
                "parse_confidence": bundle["parse_confidence"],
                "ocr_confidence": bundle["ocr_confidence"],
                "table_count": bundle["table_count"],
                "image_count": bundle["image_count"],
                "figure_count": bundle["figure_count"],
                "asset_summary": bundle["asset_summary"],
            }
        except JdfConversionError as exc:
            log.warning("JDF CI parse failed for %s, falling back: %s", filename, exc)
    elif parser == "jdf-ocr":
        # A scan: JDF CI runs its bundled OCR (tesseract.js, no per-page fee).
        # A converter failure leaves ``extracted`` None so Textract takes the
        # document below — the paid path, reached only when the free one fails.
        from ..services.jdf_converter import JdfConversionError, ocr_engine, pdf_to_parse_bundle

        try:
            bundle = pdf_to_parse_bundle(
                file_bytes, filename=filename, source_kind="scanned", ocr=ocr_engine()
            )
            extracted = {
                "jdf": bundle.get("jdf"),
                "chunks": bundle.get("chunks") or [],
                "text": bundle["text"],
                "tables": bundle["tables"],
                "images": bundle["images"],
                "figures": bundle["figures"],
                "forms": [],
                "page_count": bundle["page_count"],
                "parser_name": bundle["parser_name"],
                "source_kind": bundle["source_kind"],
                "parse_confidence": bundle["parse_confidence"],
                "ocr_confidence": bundle["ocr_confidence"],
                "table_count": bundle["table_count"],
                "image_count": bundle["image_count"],
                "figure_count": bundle["figure_count"],
                "asset_summary": bundle["asset_summary"],
            }
            if not str(bundle.get("text") or "").strip():
                # OCR ran and read nothing. That is a failed free attempt, not
                # a result: fall through to Textract like a converter error
                # does (the paid path, reached only when the free one fails).
                # Before 2026-09-23 the empty bundle was kept and the upload
                # answered 400 "0 characters" without ever trying Textract.
                log.warning("JDF OCR read no text from %s; trying Textract", filename)
                ocr_empty = True
                extracted = None
        except JdfConversionError as exc:
            log.warning("JDF OCR parse failed for %s, falling back to Textract: %s", filename, exc)
    elif use_docling:
        try:
            from ..verification.docling_extractor import extract_substrate_bytes

            temp_path = _temp_upload_dir() / f"{uuid.uuid4().hex}_{secure_filename(filename)}"
            parsed = extract_substrate_bytes(str(temp_path), file_bytes, filename)
            tables = parsed.get("extracted_tables") or []
            pages = [
                int((t.get("provenance") or {}).get("page") or 1)
                for t in tables
                if isinstance(t, dict)
            ]
            page_count = max(pages + [1])
            images = parsed.get("images") or []
            figures = parsed.get("figures") or []
            extracted = {
                "text": str(parsed.get("full_text") or "").strip(),
                "tables": tables,
                "forms": [],
                "page_count": page_count,
                "images": images,
                "figures": figures,
                "parser_name": "docling",
                "source_kind": "pdf",
                "parse_confidence": parsed.get("parse_confidence"),
                "ocr_confidence": parsed.get("ocr_confidence"),
                "table_count": len(tables),
                "image_count": len(images),
                "figure_count": len(figures),
                "asset_summary": parsed.get("asset_summary"),
            }
        except Exception:
            extracted = None

    if extracted is None:
        client = TextractClient()
        try:
            page_count = client._get_page_count(file_bytes, filename)
        except TextractError:
            if ocr_empty:
                # No Textract to fall back to: the honest answer is the OCR
                # result, not a Textract configuration error.
                raise SubstrateIngestError(
                    "Could not extract enough readable text from this file "
                    "(0 characters after OCR). Upload a clearer scan or a file with "
                    "more visible text.",
                    text_chars=0,
                )
            raise
        if page_count > TEXTRACT_MAX_PAGES:
            raise SubstrateIngestError(
                f"This document has {page_count} pages. Substrate Vault accepts up to "
                f"{TEXTRACT_MAX_PAGES} pages.",
                page_count=page_count,
            )
        try:
            extracted = client.extract_text(file_bytes, filename)
        except TextractError:
            if ocr_empty:
                raise SubstrateIngestError(
                    "Could not extract enough readable text from this file "
                    "(0 characters after OCR). Upload a clearer scan or a file with "
                    "more visible text.",
                    text_chars=0,
                )
            raise
        page_count = int(extracted.get("page_count") or page_count)
        # The Textract path has no structured-asset channel yet: empty lists,
        # unknown confidence — never fabricated scores or asset counts.
        extracted.setdefault("images", [])
        extracted.setdefault("figures", [])
        extracted.setdefault("parser_name", "textract")
        extracted.setdefault("source_kind", "pdf")
        extracted.setdefault("parse_confidence", None)
        extracted.setdefault("ocr_confidence", None)

    tables = extracted.get("tables") or []
    images = extracted.get("images") or []
    figures = extracted.get("figures") or []
    return {
        **extracted,
        "page_count": int(extracted.get("page_count") or page_count),
        "tables": tables,
        "images": images,
        "figures": figures,
        "table_count": (
            extracted.get("table_count")
            if extracted.get("table_count") is not None
            else len(tables)
        ),
        "image_count": (
            extracted.get("image_count")
            if extracted.get("image_count") is not None
            else len(images)
        ),
        "figure_count": (
            extracted.get("figure_count")
            if extracted.get("figure_count") is not None
            else len(figures)
        ),
        "asset_summary": extracted.get("asset_summary")
        or {"tables": len(tables), "images": len(images), "figures": len(figures)},
    }


def _search_chunks_for(
    extracted: dict, *, filename: str, text: str, substrate_file_id: str
) -> tuple[dict, list[dict]]:
    """The (jdf_dict, chunks) a vault upload is indexed under — the dock's shape.

    A JDF CI parse already carries jdf-cli's chunks with their page numbers;
    those are used as they are, tagged with the source filename and the vault
    row id. Any other extraction (a .txt/.md upload, Textract, Docling) has
    only text, which is split on blank lines — deterministic, so a re-upload of
    the same bytes writes the same chunks — and carries no page number, because
    none is known: a made-up ``page: 1`` on a 16-page document is a fabricated
    location. The pseudo-JDF's meta holds the text's sha256 so ``_doc_hash``
    tracks the content (its ``pages`` are empty), and a changed document is a
    new generation, not a repeat of the old one.
    """
    tags = {"source_filename": filename, "substrate_file_id": str(substrate_file_id)}
    chunks = extracted.get("chunks")
    jdf = extracted.get("jdf")
    if isinstance(chunks, list) and chunks and isinstance(jdf, dict):
        return jdf, [{**c, **tags} for c in chunks if isinstance(c, dict)]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    pseudo_jdf = {
        "$jdf": "1.0",
        "meta": {"text_sha256": text_sha, "chunking": "paragraph"},
        "pages": [{} for _ in range(int(extracted.get("page_count") or 1))],
    }
    return pseudo_jdf, [
        {"id": f"c{idx}", "text": para, "types": ["text"], **tags}
        for idx, para in enumerate(paragraphs)
    ]


def _job_advance(job_id: str | None, stage: str, **fields) -> None:
    """Record a stage on the vault upload's ingest job, when there is one.

    Same contract as ``services/pdf_ingest._job_advance``: observability, not
    control flow — a failed row write is logged and the ingest continues.
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


def ingest_substrate_file(
    project_id: str, filename: str, file_bytes: bytes, *, job_id: str | None = None
) -> dict:
    """Validate, extract, persist, verify and index a vault upload.

    Shared by the sync route and ``tasks/substrate_tasks``. Raises on
    validation/extraction errors. Returns the route payload plus, since
    2026-09-23, ``verification`` / ``z3_status`` / ``redhat_status`` and
    ``search_index``: the worker read ``entry["verification"]`` for the
    ingest job's Z3 column and the function never returned it, so every
    ``substrate_upload`` job ended with ``z3_status None`` (observed on job for
    CP00101012-1.pdf, jdf-cli, 16 pages) although verification had run. With
    ``job_id`` the ``verifying`` and ``persisting`` stages are recorded here,
    mirroring ``services/pdf_ingest``.
    """
    validate_upload_bytes(filename, file_bytes)

    extracted = extract_document_text(filename, file_bytes)
    page_count = int(extracted.get("page_count") or 1)
    if page_count > TEXTRACT_MAX_PAGES:
        raise SubstrateIngestError(
            f"This document has {page_count} pages. Substrate Vault accepts up to "
            f"{TEXTRACT_MAX_PAGES} pages.",
            page_count=page_count,
        )

    extracted_text = str(extracted.get("text") or "").strip()
    if len(extracted_text) <= MIN_EXTRACTED_TEXT_CHARS:
        raise SubstrateIngestError(
            "Could not extract enough readable text from this file "
            f"({len(extracted_text)} characters). Upload a clearer scan or "
            "a file with more visible text.",
            text_chars=len(extracted_text),
        )

    ensure_project(project_id)
    flag = flag_fields(extracted_text)
    entry = upsert_substrate_entry(
        project_id,
        filename=filename,
        page_count=page_count,
        extracted_text=extracted_text,
        tables=extracted.get("tables") or [],
        forms=extracted.get("forms") or [],
        file_size_bytes=len(file_bytes),
        parser_name=extracted.get("parser_name"),
        source_kind=extracted.get("source_kind"),
        parse_confidence=extracted.get("parse_confidence"),
        ocr_confidence=extracted.get("ocr_confidence"),
        table_count=extracted.get("table_count"),
        image_count=extracted.get("image_count"),
        figure_count=extracted.get("figure_count"),
        asset_summary=extracted.get("asset_summary"),
        **flag,
    )
    remember_vault_file(project_id, str(entry["id"]), filename=filename, text=extracted_text)

    # The chunks this document is searched and verified by: jdf-cli's own when
    # the parser produced them, paragraphs of the text otherwise.
    index_jdf, index_chunks = _search_chunks_for(
        extracted, filename=filename, text=extracted_text, substrate_file_id=str(entry["id"])
    )

    _job_advance(
        job_id,
        "verifying",
        parser_name=extracted.get("parser_name"),
        source_kind=extracted.get("source_kind"),
        page_count=page_count,
        parse_confidence=extracted.get("parse_confidence"),
        ocr_confidence=extracted.get("ocr_confidence"),
        substrate_file_id=str(entry["id"]),
    )
    # Shared verification hook: Z3 + Red-Hat run here and only here
    # (services/verification). The vault ingest has no Assure tree of its
    # own, so the hook builds one from the chunks above and attaches its
    # result to the pseudo-bundle. Guarded: the ingest must not fail on
    # verification.
    try:
        verification_bundle = {
            "filename": filename,
            "page_count": page_count,
            "text": extracted_text,
            "chunks": [dict(c) for c in index_chunks],
        }
        verification = run_verification_after_parse(verification_bundle)
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

    # Index for search the way the dock ingest does (remember_jdf_document,
    # kind "jdf_chunk"). Until 2026-09-23 a vault upload reached only
    # remember_vault_file (tags, filtered out by _tenant_chunk_candidates), so
    # POST /jdf/search returned nothing from a document uploaded in SOURCES.
    # doc_id is the filename — the key the dock uses and the key the vault row is
    # upserted on — so both paths index one document per filename per project.
    # Best-effort: the vault row is the deliverable; a refused index is reported,
    # not fatal.
    search_index: dict = {"indexed": False, "doc_id": filename}
    try:
        search_index = {
            "indexed": True,
            **remember_jdf_document(filename, index_jdf, index_chunks, tenant_id=project_id),
        }
    except Exception as exc:
        log.exception("Substrate ingest: search index write failed for %s", filename)
        search_index["error"] = f"{exc.__class__.__name__}: {str(exc)[:300]}"

    # Stage the parsed artifact into OMP immediately after the row write.
    # Confidence rides through explicitly — the OMP layer attaches it with
    # is-not-None guards, so a parser-reported 0.0 survives and unknown stays
    # None. Staging is best-effort, but a failure is never swallowed silently:
    # it is logged with the exception so an ingest whose artifact never
    # staged is diagnosable after the fact.
    omp_artifact_id = None
    try:
        substrate_result = {
            "id": entry["id"],
            "filename": filename,
            "page_count": page_count,
            "text": entry["extracted_text"],
            "tables": entry["tables"],
            "images": extracted.get("images") or [],
            "figures": extracted.get("figures") or [],
            "forms": entry.get("forms") or [],
            "size_bytes": entry.get("file_size_bytes", len(file_bytes)),
            "is_image": Path(filename).suffix.lower() in IMAGE_EXTENSIONS,
            "parser_name": extracted.get("parser_name"),
            "source_kind": extracted.get("source_kind"),
            "table_count": extracted.get("table_count"),
            "image_count": extracted.get("image_count"),
            "figure_count": extracted.get("figure_count"),
            "asset_summary": extracted.get("asset_summary"),
        }
        omp_artifact = build_omp_artifact_from_parse(
            project_id,
            substrate_result,
            parse_confidence=extracted.get("parse_confidence"),
            ocr_confidence=extracted.get("ocr_confidence"),
            parser_name=extracted.get("parser_name"),
            source_kind=extracted.get("source_kind"),
            page_count=page_count,
            table_count=extracted.get("table_count"),
            image_count=extracted.get("image_count"),
            figure_count=extracted.get("figure_count"),
            asset_summary=extracted.get("asset_summary"),
            verification=verification,
        )
        store_omp_artifact(project_id, omp_artifact)
        omp_artifact_id = omp_artifact.artifact_id
        # Link the row to its staged artifact.
        upsert_substrate_entry(
            project_id,
            filename=filename,
            page_count=page_count,
            extracted_text=extracted_text,
            tables=extracted.get("tables") or [],
            forms=extracted.get("forms") or [],
            file_size_bytes=len(file_bytes),
            parser_name=extracted.get("parser_name"),
            source_kind=extracted.get("source_kind"),
            parse_confidence=extracted.get("parse_confidence"),
            ocr_confidence=extracted.get("ocr_confidence"),
            table_count=extracted.get("table_count"),
            image_count=extracted.get("image_count"),
            figure_count=extracted.get("figure_count"),
            asset_summary=extracted.get("asset_summary"),
            omp_artifact_id=omp_artifact_id,
            **flag,
        )
    except Exception as exc:
        log.exception("Substrate ingest: OMP parse artifact staging failed: %s", exc)

    # Parsure intake report for the Sources-panel path too. Until 2026-09-25
    # only the import-pdf route (services/pdf_ingest) wrote one, so a document
    # uploaded from the shell's Sources pane — the main path — had no fields,
    # no quality score and an empty Fields tab. Same guarded hook; the router's
    # intake dict (material, modality, visual probe, Laya) is computed here
    # because this path called select_parser directly.
    parsure_report_id = None
    try:
        try:
            from ..services.parser_router import route_intake
            from ..services.v1_orchestrator import run_after_parse
        except ImportError:
            from services.parser_router import route_intake
            from services.v1_orchestrator import run_after_parse
        try:
            intake = route_intake(file_bytes, filename)
        except Exception:
            log.exception("parsure: route_intake failed for %s; report without visual probe", filename)
            intake = None
        parsure_bundle = {
            **{k: v for k, v in extracted.items() if k not in ("jdf", "chunks")},
            "jdf": index_jdf,
            "chunks": [dict(c) for c in index_chunks],
            "text": extracted_text,
            "page_count": page_count,
            "parser_name": extracted.get("parser_name"),
            "source_kind": extracted.get("source_kind"),
        }
        parsure = run_after_parse(
            project_id,
            bundle=parsure_bundle,
            verification=verification,
            filename=filename,
            file_bytes=file_bytes,
            result={"document_id": str(entry["id"]), "revision_id": None, "version": None},
            job_id=job_id,
            intake=intake,
        )
        parsure_report_id = (parsure or {}).get("report_id") if isinstance(parsure, dict) else None
    except Exception:
        log.exception("parsure report failed for %s; the vault row is unaffected", filename)

    return {
        "ok": True,
        "id": entry["id"],
        "filename": filename,
        "parsure_report_id": parsure_report_id,
        "page_count": page_count,
        "text": entry["extracted_text"],
        "tables": entry["tables"],
        "images": extracted.get("images") or [],
        "figures": extracted.get("figures") or [],
        "parser_name": extracted.get("parser_name"),
        "source_kind": extracted.get("source_kind"),
        "parse_confidence": extracted.get("parse_confidence"),
        "ocr_confidence": extracted.get("ocr_confidence"),
        "table_count": extracted.get("table_count"),
        "image_count": extracted.get("image_count"),
        "figure_count": extracted.get("figure_count"),
        "asset_summary": extracted.get("asset_summary"),
        "size_bytes": entry.get("file_size_bytes", len(file_bytes)),
        "is_image": Path(filename).suffix.lower() in IMAGE_EXTENSIONS,
        "omp_artifact_id": omp_artifact_id,
        "verification": verification,
        "z3_status": (verification or {}).get("z3_status"),
        "redhat_status": (verification or {}).get("redhat_status"),
        "search_index": search_index,
        **flag_response(flag),
    }


def register_substrate_routes(app) -> None:
    try:
        from ..rate_limits import limiter
    except ImportError:
        from rate_limits import limiter

    @app.post("/api/projects/<project_id>/substrate/upload")
    @project_ownership_required
    def substrate_upload(project_id: str):
        request_id = str(uuid.uuid4())
        audit = get_audit_logger()
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify({"ok": False, "error": "No file uploaded."}), 400

        filename = upload.filename.strip()
        file_bytes = upload.read()
        if not file_bytes:
            return jsonify({"ok": False, "error": "Empty file."}), 400

        try:
            validate_upload_bytes(filename, file_bytes)
        except UploadRejectedError as exc:
            return jsonify({"ok": False, "error": str(exc)}), exc.http_status

        # The project row first, on both paths: the audit row and the vault
        # row reference projects(id), and the sync path used to write them for
        # a project that did not exist yet ("AUDIT ROW DROPPED … audit_log_
        # project_id_fkey" on every first upload in the test run, 2026-09-23).
        try:
            from ..db.jdf_repository import ensure_project as _ensure_project
        except ImportError:
            from db.jdf_repository import ensure_project as _ensure_project
        _ensure_project(project_id)

        if _substrate_async_enabled():
            # Staged in the object store, not on this replica's disk: the
            # worker that parses it may be on another host.
            try:
                from ..services.object_store import get_object_store, upload_key
            except ImportError:
                from services.object_store import get_object_store, upload_key
            object_key = upload_key(project_id, filename)
            # Content type follows the file: uploads are PDFs, images or text
            # since the multimodal intake (2026-09-25), no longer PDF only.
            import mimetypes

            get_object_store().put_bytes(
                object_key,
                file_bytes,
                content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
            )
            try:
                from ..db.ingest_jobs_repository import create_job, set_task
                from ..db.jdf_repository import ensure_project as _ensure_project
            except ImportError:
                from db.ingest_jobs_repository import create_job, set_task
                from db.jdf_repository import ensure_project as _ensure_project
            _ensure_project(project_id)
            job_id = create_job(
                project_id,
                kind="substrate_upload",
                filename=filename,
                object_key=object_key,
                size_bytes=len(file_bytes),
            )
            try:
                from ..tasks.substrate_tasks import process_substrate_upload
            except ImportError:
                from tasks.substrate_tasks import process_substrate_upload
            task = process_substrate_upload.apply_async(
                args=[project_id, object_key, filename, job_id],
                queue="parse",
            )
            set_task(job_id, task.id)
            audit.log_audit(
                request_id,
                project_id,
                "SUBSTRATE_UPLOAD",
                success=True,
                details={"filename": filename, "async": True, "task_id": task.id},
            )
            return (
                jsonify(
                    {
                        "ok": True,
                        "task_id": task.id,
                        "job_id": job_id,
                        "status": "queued",
                        "status_url": f"/api/tasks/{task.id}",
                        "job_url": f"/api/projects/{project_id}/ingest-jobs/{job_id}",
                    }
                ),
                202,
            )

        try:
            payload = ingest_substrate_file(project_id, filename, file_bytes)
        except SubstrateIngestError as exc:
            audit.log_audit(
                request_id,
                project_id,
                "SUBSTRATE_UPLOAD",
                success=False,
                error_message=str(exc),
            )
            body: dict = {"ok": False, "error": str(exc)}
            if exc.text_chars is not None:
                body["text_chars"] = exc.text_chars
            if exc.page_count is not None:
                body["page_count"] = exc.page_count
                body["max_pages"] = TEXTRACT_MAX_PAGES
            return jsonify(body), 400
        except TextractError as exc:
            audit.log_audit(
                request_id,
                project_id,
                "SUBSTRATE_UPLOAD",
                success=False,
                error_message=str(exc),
            )
            return jsonify({"ok": False, "error": str(exc)}), 503

        audit.log_audit(
            request_id,
            project_id,
            "SUBSTRATE_UPLOAD",
            success=True,
            details={
                "filename": filename,
                "page_count": payload.get("page_count"),
                "text_chars": len(payload.get("text") or ""),
            },
        )
        return jsonify(payload)

    @app.get("/api/projects/<project_id>/substrate")
    @project_ownership_required
    def substrate_list(project_id: str):
        entries = list_substrate_for_project(project_id)
        if not entries:
            return jsonify({"ok": True, "files": []})

        doc = fetch_latest_jdf_or_empty(project_id)
        claims_by_source_id: dict[str, int] = {}
        for node in flatten_nodes(doc):
            for prov in node.get("provenance") or []:
                if not isinstance(prov, dict):
                    continue
                source_id = str(prov.get("source_id") or "").strip()
                if source_id:
                    claims_by_source_id[source_id] = claims_by_source_id.get(source_id, 0) + 1

        files = [
            {
                **flag_response(entry),
                "claims_count": claims_by_source_id.get(entry["id"], 0),
            }
            for entry in entries
        ]
        return jsonify({"ok": True, "files": files})

    @app.get("/api/projects/<project_id>/substrate/<file_id>")
    @project_ownership_required
    def substrate_get(project_id: str, file_id: str):
        entry = fetch_substrate_entry(project_id, file_id)
        if not entry:
            return jsonify({"ok": False, "error": "File not found."}), 404
        return jsonify(
            {
                "ok": True,
                "file": {
                    "id": entry.get("id"),
                    "filename": entry.get("filename"),
                    "page_count": entry.get("page_count"),
                    "extracted_text": entry.get("extracted_text") or "",
                    "included": entry.get("included", True),
                },
            }
        )

    @app.delete("/api/projects/<project_id>/substrate/<file_id>")
    @project_ownership_required
    def substrate_delete(project_id: str, file_id: str):
        request_id = str(uuid.uuid4())
        audit = get_audit_logger()
        # Read the filename first: it is the search index's doc_id, and the row
        # is gone once the delete has run.
        existing = fetch_substrate_entry(project_id, file_id)
        removed = delete_substrate_entry(project_id, file_id)
        if not removed:
            return jsonify({"ok": False, "error": "File not found."}), 404
        # The intake report leaves the lists with its document (audit rows stay).
        try:
            try:
                from ..db.parsure_repository import mark_document_deleted
            except ImportError:
                from db.parsure_repository import mark_document_deleted
            mark_document_deleted(
                project_id, str(file_id), filename=(existing or {}).get("filename") if isinstance(existing, dict) else None
            )
        except Exception:
            log.exception("Substrate delete: could not hide the intake report for %s", file_id)
        # A deleted source must stop answering searches: forget its chunks
        # (durable rows and, when configured, OMP) under the same doc_id the
        # ingest wrote them. Guarded — the row is already gone, and a failed
        # index cleanup is logged, not turned into a 500 for a delete that
        # happened.
        forgotten: dict | None = None
        try:
            if existing and existing.get("filename"):
                forgotten = forget_jdf_document(str(existing["filename"]), tenant_id=project_id)
        except Exception:
            log.exception("Substrate delete: search index cleanup failed for %s", file_id)
        audit.log_audit(
            request_id,
            project_id,
            "SUBSTRATE_DELETE",
            success=True,
            details={"file_id": file_id, "search_index": forgotten},
        )
        return jsonify({"ok": True, "id": file_id, "search_index": forgotten})

    @app.patch("/api/projects/<project_id>/substrate/<file_id>")
    @project_ownership_required
    def substrate_patch(project_id: str, file_id: str):
        data = request.get_json(silent=True) or {}
        if "included" not in data:
            return jsonify({"ok": False, "error": "Missing 'included' field."}), 400
        updated = set_substrate_included(project_id, file_id, bool(data.get("included")))
        if not updated:
            return jsonify({"ok": False, "error": "File not found."}), 404
        return jsonify({"ok": True, "id": file_id, "included": bool(data.get("included"))})
