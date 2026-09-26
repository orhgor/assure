"""JDF memory (knowledge-base) ingest + search endpoints, project-scoped."""
from __future__ import annotations

import logging
import os
import re
import shutil

from flask import jsonify, request

try:
    from ..middleware import project_ownership_required
except ImportError:
    from middleware import project_ownership_required

try:
    from ..db.jdf_repository import ensure_project
    from ..db.substrate_repository import (
        fetch_substrate_entries_by_ids,
        list_substrate_for_project,
        upsert_substrate_entry,
    )
    from ..lib.textract import TextractClient
    from ..services.compile_guard import flag_fields, flag_response
    from ..services.jdf_converter import JDF_BIN, JdfConversionError, chunks_to_text, pdf_to_parse_bundle
    from ..services.jdf_memory import OmpUnavailable, remember_jdf_document, search_jdf_chunks
    from ..services.omp import build_omp_artifact_from_parse, store_omp_artifact
    from ..services.omp_memory import remember_vault_file
    from ..services.parser_router import select_parser
    from ..services.verification import run_verification_after_parse
except ImportError:
    from db.jdf_repository import ensure_project
    from db.substrate_repository import (
        fetch_substrate_entries_by_ids,
        list_substrate_for_project,
        upsert_substrate_entry,
    )
    from lib.textract import TextractClient
    from services.compile_guard import flag_fields, flag_response
    from services.jdf_converter import JDF_BIN, JdfConversionError, chunks_to_text, pdf_to_parse_bundle
    from services.jdf_memory import OmpUnavailable, remember_jdf_document, search_jdf_chunks
    from services.omp import build_omp_artifact_from_parse, store_omp_artifact
    from services.parser_router import select_parser
    from services.verification import run_verification_after_parse

log = logging.getLogger(__name__)

# FIX 3's cap, configurable so a deployment can raise it without a code
# change; the default stays the 25MB the route shipped with.
MAX_UPLOAD_BYTES = int(os.environ.get("ASSURE_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))


def _textract_bundle_for_ingest(pdf_bytes: bytes, filename: str) -> dict:
    """Bundle shape for the router's "textract" decision in the memory ingest.

    The router decided this PDF is a scan, so Textract reads it and the text
    is wrapped as a minimal JDF (one chunk per paragraph) so the downstream
    vault row, OMP staging, and chunk indexing all run on the same shape a
    JDF CI parse produces. Parse/OCR confidence stays None — Textract reports
    none we trust, and None is the honest unknown, never a fabricated score.
    """
    extracted = TextractClient().extract_text(pdf_bytes, filename)
    text = str(extracted.get("text") or "").strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks = [
        {"id": f"c{idx}", "text": para, "types": ["text"], "page": 1}
        for idx, para in enumerate(paragraphs)
    ]
    page_count = int(extracted.get("page_count") or 1)
    return {
        "jdf": {"$jdf": "1.0", "meta": {}, "pages": [{} for _ in range(page_count)]},
        "chunks": chunks,
        "text": text,
        "page_count": page_count,
        "parser_name": "textract",
        "source_kind": "pdf",
        "parse_confidence": None,
        "ocr_confidence": None,
        "tables": extracted.get("tables") or [],
        "images": [],
        "figures": [],
        "table_count": len(extracted.get("tables") or []),
        "image_count": 0,
        "figure_count": 0,
        "asset_summary": {
            "tables": len(extracted.get("tables") or []),
            "images": 0,
            "figures": 0,
        },
        "filename": filename,
    }


def _is_text_like_content(file_bytes: bytes, filename: str) -> bool:
    """Determine if content is text-like without relying on extension.

    The router's extension hint answers the common case, but a file that
    arrives without a known extension still needs a decision here, and that
    decision must come from the bytes: content probing, not the name. A PDF
    magic header, or NUL bytes in the first kilobyte, disqualifies; anything
    that decodes as UTF-8 is text.
    """
    if file_bytes[:5] == b"%PDF-":
        return False
    if b"\x00" in file_bytes[:1024]:
        return False
    try:
        file_bytes.decode("utf-8")
        return True
    except (UnicodeDecodeError, ValueError):
        return False


def _text_bundle_for_ingest(file_bytes: bytes, filename: str) -> dict:
    """Bundle shape for a text-like file the router routed to the JDF path.

    Same shape ``_textract_bundle_for_ingest`` produces for a scan: the text
    is wrapped as a minimal JDF (one chunk per paragraph) so the downstream
    vault row, OMP staging, and chunk indexing all run on the same shape a
    JDF CI parse produces — no second document model, no binary parser. The
    text is already the document, so the parse is exact: confidence 1.0.
    """
    text_content = file_bytes.decode("utf-8", errors="replace")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text_content) if p.strip()]
    chunks = [
        {"id": f"c{idx}", "text": para, "types": ["text"], "page": 1}
        for idx, para in enumerate(paragraphs)
    ]
    return {
        "jdf": {"$jdf": "1.0", "meta": {}, "pages": [{}]},
        "chunks": chunks,
        "text": chunks_to_text(chunks) if chunks else text_content,
        "page_count": 1,
        "parser_name": "text",
        "source_kind": "text",
        "parse_confidence": 1.0,
        "ocr_confidence": None,
        "tables": [],
        "images": [],
        "figures": [],
        "table_count": 0,
        "image_count": 0,
        "figure_count": 0,
        "asset_summary": {"tables": 0, "images": 0, "figures": 0},
        "filename": filename,
    }


def _store_grounding_source(
    project_id: str,
    filename: str,
    chunks: list[dict],
    *,
    size_bytes: int,
    page_count: int,
    parse_confidence: float | None = None,
    ocr_confidence: float | None = None,
    parser_name: str | None = None,
    source_kind: str | None = None,
    table_count: int | None = None,
    image_count: int | None = None,
    figure_count: int | None = None,
    asset_summary: dict | None = None,
    omp_artifact_id: str | None = None,
) -> None:
    """Leave the ingested PDF as a compile source for this project.

    A compile grounds only through POST /draft's substrate_file_ids, and
    fetch_substrate_entries_by_ids reads substrate_vault, so an ingest that
    indexed chunks without a vault row left the document ungrounded: the source
    panel read "0 sources", the draft carried substrate_file_ids: [] and Red-Hat
    skipped with "no substrate or empty draft". The text is already extracted
    here (the chunks are the document), so no Textract/Docling pass is involved
    and the vault upload route keeps its .txt/.md restriction untouched.

    Parse metadata (parser name, source kind, parse/OCR confidence, structured-
    asset counts, OMP artifact id) is persisted on the row: the vault row is the
    record of how this document was parsed, not just a bag of text.

    An ingest whose chunks carry no text still leaves the row: a PDF the
    converter returned no text for is a source the user uploaded, and a missing
    row reads as "no sources uploaded" — a different, wrong answer. Such a row
    keeps the text already on file for that filename (an empty field when there
    is none) instead of blanking a good extraction with an empty one.
    """
    text = chunks_to_text(chunks)
    if not text:
        for existing in list_substrate_for_project(project_id):
            if str(existing.get("filename") or "") == filename:
                found = fetch_substrate_entries_by_ids(
                    project_id, [str(existing.get("id") or "")]
                )
                if found:
                    text = str(found[0].get("extracted_text") or "")
                break
    ensure_project(project_id)
    flag = flag_fields(text)
    if flag["instruction_like"]:
        log.warning(
            "[substrate-scan] %s flagged instruction-like: %s",
            filename,
            ", ".join(flag["instruction_hits"]),
        )
    entry = upsert_substrate_entry(
        project_id,
        filename=filename,
        page_count=page_count,
        extracted_text=text,
        file_size_bytes=size_bytes,
        parser_name=parser_name,
        source_kind=source_kind,
        parse_confidence=parse_confidence,
        ocr_confidence=ocr_confidence,
        table_count=table_count,
        image_count=image_count,
        figure_count=figure_count,
        asset_summary=asset_summary,
        omp_artifact_id=omp_artifact_id,
        **flag,
    )
    remember_vault_file(
        project_id,
        str(entry["id"]),
        filename=filename,
        text=text,
    )
    return flag, entry


def register_jdf_memory_routes(app) -> None:
    @app.post("/api/projects/<project_id>/jdf/ingest")
    @project_ownership_required
    def jdf_ingest(project_id: str):
        # FIX 3: size cap before reading body bytes.
        content_length = request.content_length or 0
        if content_length > MAX_UPLOAD_BYTES:
            return jsonify({"error": "file too large (max 25MB)"}), 413
        if "file" not in request.files:
            return jsonify({"error": "no file"}), 400
        f = request.files["file"]
        doc_id = request.form.get("doc_id") or f.filename
        try:
            pdf_bytes = f.read()
            if not pdf_bytes:
                return jsonify({"error": "Empty file."}), 400
            # Parser *selection* is the router's call (services/parser_router):
            # JDF CI for a text-layer PDF, JDF CI + OCR for a scan (Textract only
            # when OCR fails or reads nothing), a direct text wrap when the bytes
            # are text-like. This handler executes the decision — no inline
            # parser branching beyond that. Guarded: an unexpected parse failure
            # is the client's bad input, not a server crash, so it answers 422
            # with a safe message and logs the real cause server-side.
            try:
                _parser = select_parser(pdf_bytes, filename=f.filename)

                if _parser == "textract":
                    bundle = _textract_bundle_for_ingest(pdf_bytes, f.filename)
                elif _parser == "jdf-ocr":
                    try:
                        bundle = pdf_to_parse_bundle(
                            pdf_bytes,
                            filename=f.filename,
                            source_kind="scanned",
                            ocr=ocr_engine(),
                        )
                    except JdfConversionError as ocr_exc:
                        log.warning(
                            "JDF OCR parse failed for %s, falling back to Textract: %s",
                            f.filename,
                            ocr_exc,
                        )
                        bundle = _textract_bundle_for_ingest(pdf_bytes, f.filename)
                    else:
                        if not str(bundle.get("text") or "").strip():
                            # OCR ran and read nothing: the paid path gets the
                            # document; without Textract the OCR result stands
                            # (same rule as routers/substrate, services/pdf_ingest).
                            log.warning("JDF OCR read no text from %s; trying Textract", f.filename)
                            try:
                                bundle = _textract_bundle_for_ingest(pdf_bytes, f.filename)
                            except Exception:
                                return jsonify({"error": "Could not extract readable text from this document (0 characters after OCR)."}), 400
                elif _parser == "jdf":
                    if _is_text_like_content(pdf_bytes, f.filename):
                        bundle = _text_bundle_for_ingest(pdf_bytes, f.filename)
                    else:
                        # JDF is the default PDF parser: one bundle call carries
                        # the JDF document, its chunks, and the parse/OCR
                        # confidence a route or OMP staging needs — not just
                        # the raw JDF tree.
                        bundle = pdf_to_parse_bundle(
                            pdf_bytes, filename=f.filename
                        )
                else:
                    return jsonify({"error": "Unsupported document type"}), 400

            except JdfConversionError:
                # The converter's stderr names the internal tool and a
                # byte-level reason. That belongs in the log, not in the
                # response: the client gets the same plain message the vault
                # upload route uses for the same class of input, with the
                # status the input deserves.
                log.exception("jdf ingest failed")
                return jsonify({"error": "Invalid or malformed document"}), 400

            except Exception as parse_exc:
                log.exception(
                    "Parse failed for project %s, file %s: %s",
                    project_id,
                    f.filename,
                    type(parse_exc).__name__,
                )
                return jsonify(
                    {"error": "Document processing failed. Please try another file."}
                ), 422
            jdf_dict = bundle["jdf"]
            chunks = bundle["chunks"]
            # Shared verification hook: Z3 + Red-Hat run here and only here
            # (services/verification) — the hook builds the Assure tree from
            # the chunks this bundle carries and attaches its result to the
            # bundle. Guarded: the ingest must not fail on verification.
            try:
                verification = run_verification_after_parse(bundle)
            except Exception:
                log.exception("post-parse verification failed; storing parse only")
                verification = None
            # Before the chunk index: a compile grounds from the project's
            # substrate_file_ids, so the vault row is what makes this ingest
            # visible to the source panel and to the draft pipeline.
            flag, entry = _store_grounding_source(
                project_id,
                f.filename,
                chunks,
                size_bytes=len(pdf_bytes),
                page_count=bundle["page_count"],
                parse_confidence=bundle["parse_confidence"],
                ocr_confidence=bundle["ocr_confidence"],
                parser_name=bundle["parser_name"],
                source_kind=bundle["source_kind"],
                table_count=bundle["table_count"],
                image_count=bundle["image_count"],
                figure_count=bundle["figure_count"],
                asset_summary=bundle["asset_summary"],
            )
            # Stage the parsed data into OMP alongside the vault row, the
            # same artifact shape the Textract substrate route stages
            # (services/omp.build_omp_artifact_from_parse), so a jdf-cli
            # ingest is visible to OMP the same way a Textract one is.
            # Confidence is passed explicitly with is-not-None semantics in
            # the OMP layer: a parser-reported 0.0 survives, unknown stays
            # None.
            omp_artifact_id = None
            try:
                substrate_result = {
                    "id": entry["id"],
                    "filename": f.filename,
                    "page_count": bundle["page_count"],
                    "text": entry.get("extracted_text", ""),
                    "tables": bundle["tables"],
                    "images": bundle["images"],
                    "figures": bundle["figures"],
                    "forms": entry.get("forms") or [],
                    "size_bytes": len(pdf_bytes),
                    "is_image": False,
                    "parser_name": bundle["parser_name"],
                    "source_kind": bundle["source_kind"],
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
                # Link the row to its staged artifact — the vault row is the
                # durable record, so it names the OMP artifact it staged.
                upsert_substrate_entry(
                    project_id,
                    filename=f.filename,
                    page_count=bundle["page_count"],
                    extracted_text=entry.get("extracted_text") or "",
                    tables=entry.get("tables") or [],
                    forms=entry.get("forms") or [],
                    file_size_bytes=len(pdf_bytes),
                    parser_name=bundle["parser_name"],
                    source_kind=bundle["source_kind"],
                    parse_confidence=bundle["parse_confidence"],
                    ocr_confidence=bundle["ocr_confidence"],
                    table_count=bundle["table_count"],
                    image_count=bundle["image_count"],
                    figure_count=bundle["figure_count"],
                    asset_summary=bundle["asset_summary"],
                    omp_artifact_id=omp_artifact_id,
                    **flag,
                )
            except Exception:
                # OMP staging is best-effort — the vault row above already
                # grounds the compile, so a staging failure here must not
                # fail the ingest, only be logged clearly.
                log.exception("jdf ingest: OMP parse artifact staging failed")
            result = remember_jdf_document(doc_id, jdf_dict, chunks, tenant_id=project_id)
            return jsonify(
                {
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
                    **flag_response(flag),
                }
            )
        except OmpUnavailable:  # FIX 4
            return jsonify({"error": "index temporarily unavailable, try again"}), 503
        except JdfConversionError:
            # The converter's stderr names the internal tool and a byte-level
            # reason. That belongs in the log, not in the response: the client
            # gets the same plain message the vault upload route uses for the
            # same class of input, with the status the input deserves.
            log.exception("jdf ingest failed")
            return jsonify({"error": "Invalid or malformed PDF"}), 400

    @app.post("/api/projects/<project_id>/jdf/search")
    @project_ownership_required
    def jdf_search(project_id: str):
        body = request.get_json(silent=True) or {}
        q = (body.get("query") or "").strip()
        if not q:
            return jsonify({"error": "query required"}), 400
        try:
            limit = int(body.get("limit", 20))
        except (TypeError, ValueError):
            limit = 20
        results = search_jdf_chunks(q, tenant_id=project_id, limit=limit)
        return jsonify({"ok": True, "project_id": project_id, "count": len(results), "results": results})

    @app.get("/api/projects/<project_id>/jdf/health")
    @project_ownership_required
    def jdf_health(project_id: str):
        ok = bool(JDF_BIN and (os.path.exists(JDF_BIN) or shutil.which("jdf")))
        return jsonify({"ok": ok, "project_id": project_id, "jdf_bin": JDF_BIN}), (200 if ok else 503)