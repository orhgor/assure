"""Project document export routes (sync — runs in WSGI worker threadpool)."""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
import uuid
import zipfile

from flask import Response, jsonify

try:
    from ..db.jdf_repository import current_document_version, fetch_latest_jdf_or_empty
    from ..exporters.docx_ast import export_jdf_to_docx
    from ..exporters.pdf_ast import export_jdf_to_pdf
    from ..exporters.text_ast import jdf_to_html, jdf_to_markdown
    from ..lib.logger import get_audit_logger
    from ..middleware import project_ownership_required
    from ..services.jdf_sidecar import build_jdf_sidecar
    from ..services.verification_dossier import (
        PdfRendererUnavailable,
        build_dossier,
        render_pdf,
        renderer_status,
        verification_state_json,
    )
except ImportError:
    from db.jdf_repository import current_document_version, fetch_latest_jdf_or_empty
    from exporters.docx_ast import export_jdf_to_docx
    from exporters.pdf_ast import export_jdf_to_pdf
    from exporters.text_ast import jdf_to_html, jdf_to_markdown
    from lib.logger import get_audit_logger
    from middleware import project_ownership_required
    from services.jdf_sidecar import build_jdf_sidecar
    from services.verification_dossier import (
        PdfRendererUnavailable,
        build_dossier,
        render_pdf,
        renderer_status,
        verification_state_json,
    )

_SAFE_NAME = re.compile(r"[^\w\-]+")


def _doc_title(tree: dict) -> str:
    meta = tree.get("meta") or {}
    raw = str(
        meta.get("title") or meta.get("project_id") or tree.get("document_id") or "assure-document"
    )
    cleaned = _SAFE_NAME.sub("-", raw.strip()).strip("-") or "assure-document"
    return cleaned[:80]


def _dossier_base(project_id: str, version: int) -> str:
    """``<project>-<version>-dossier`` — the stem both halves of the export share.

    Named from the project and the revision, not the document title: the two files
    travel together, and a reader holding one has to be able to tell which export
    the other belongs to. A title is prose and repeats across projects; the id and
    the version do not.
    """
    slug = _SAFE_NAME.sub("-", str(project_id or "").strip()).strip("-") or "assure"
    return f"{slug}-{int(version or 0)}-dossier"


def _bundle_zip(*members: tuple[str, bytes]) -> bytes:
    """One download holding the export pair, so neither file can arrive alone.

    ``ZIP_DEFLATED`` with the default level: the PDF is already compressed and the
    JDF is text, so the saving is on the sidecar and the cost is bounded.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members:
            archive.writestr(name, payload)
    return buffer.getvalue()


def _renderer_unavailable(exc: PdfRendererUnavailable):
    """503 with each engine's answer. A missing renderer is a server condition,
    not a document property: until 2026-09-26 the export fell through to a text
    writer and a customer received a Helvetica dump titled as a PDF dossier."""
    return jsonify(
        {"ok": False, "error": "PDF renderer unavailable on this server", "detail": exc.detail}
    ), 503


def _require_pdf_renderer() -> None:
    """Raise :class:`PdfRendererUnavailable` unless an engine can run.

    Used before ``exporters.pdf_ast.export_jdf_to_pdf`` (the document-body PDF),
    whose own probes still degrade to text when neither engine is present.
    """
    status = renderer_status()
    if not any(v.startswith("ok") for v in status.values()):
        raise PdfRendererUnavailable(status)


def register_export_routes(app) -> None:
    try:
        from ..rate_limits import limiter
    except ImportError:
        from rate_limits import limiter

    @app.get("/api/projects/<project_id>/export")
    @limiter.limit("10 per minute")
    @project_ownership_required
    def export_project_document(project_id: str):
        """
        Sync endpoint: Gunicorn/Flask executes this in a worker thread.
        Keeps synchronous SQLite I/O and python-docx CPU work off concurrent SSE streams.
        """
        from flask import request

        request_id = str(uuid.uuid4())
        start_time = time.perf_counter()
        audit = get_audit_logger()
        fmt = (request.args.get("format") or "docx").strip().lower()
        include_citations_raw = (request.args.get("include_citations") or "true").strip().lower()
        include_citations = include_citations_raw not in ("0", "false", "no")
        if fmt == "json":
            tree = fetch_latest_jdf_or_empty(project_id)
            try:
                from ..services.audit_bundle import compute_export_gate
            except ImportError:
                from services.audit_bundle import compute_export_gate
            gate = compute_export_gate(project_id, tree)
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            audit.log_audit(
                request_id,
                project_id,
                "EXPORT_JSON",
                success=True,
                duration_ms=duration_ms,
                details={"format": fmt},
            )
            # NOTE: the outer "ok" here is REQUEST success, not the verification
            # gate. The gate is surfaced separately under gate_status.
            return jsonify(
                {
                    "ok": True,
                    "document": tree,
                    "gate_status": gate["gate_status"],
                    "z3_status": gate["z3_status"],
                    "unverified": gate["unverified"],
                    "unverified_reason": gate["unverified_reason"],
                    "provenance_stats": dict(gate["provenance_stats"]),
                }
            )

        tree = fetch_latest_jdf_or_empty(project_id)
        filename_base = _doc_title(tree)

        if fmt in ("md", "markdown"):
            body = jdf_to_markdown(tree)
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            audit.log_audit(
                request_id,
                project_id,
                "EXPORT_MD",
                success=True,
                duration_ms=duration_ms,
                details={"format": "md", "filename": filename_base + ".md"},
            )
            return Response(
                body,
                mimetype="text/markdown; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{filename_base}.md"'},
            )

        if fmt == "html":
            body = jdf_to_html(tree)
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            audit.log_audit(
                request_id,
                project_id,
                "EXPORT_HTML",
                success=True,
                duration_ms=duration_ms,
                details={"format": "html", "filename": filename_base + ".html"},
            )
            return Response(
                body,
                mimetype="text/html; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{filename_base}.html"'},
            )

        if fmt == "pdf":
            audit_bundle = (request.args.get("audit_bundle") or "").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            try:
                if audit_bundle:
                    try:
                        from ..services.audit_bundle import export_audit_bundle_pdf
                    except ImportError:
                        from services.audit_bundle import export_audit_bundle_pdf
                    pdf_bytes = export_audit_bundle_pdf(project_id, tree)
                    filename = f"{filename_base}-audit-report.pdf"
                    action = "EXPORT_AUDIT_PDF"
                else:
                    _require_pdf_renderer()
                    pdf_bytes = export_jdf_to_pdf(tree)
                    filename = f"{filename_base}.pdf"
                    action = "EXPORT_PDF"
            except PdfRendererUnavailable as exc:
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                audit.log_audit(
                    request_id,
                    project_id,
                    "EXPORT_PDF",
                    success=False,
                    duration_ms=duration_ms,
                    error_message=str(exc),
                    details={"format": fmt, "renderers": exc.detail},
                )
                return _renderer_unavailable(exc)
            except Exception as exc:
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                audit.log_exception(
                    request_id,
                    project_id,
                    "EXPORT_PDF",
                    exc,
                    duration_ms=duration_ms,
                    details={"format": fmt},
                )
                return jsonify({"ok": False, "error": str(exc)}), 500
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            audit.log_audit(
                request_id,
                project_id,
                action,
                success=True,
                duration_ms=duration_ms,
                details={"format": "pdf", "filename": filename, "audit_bundle": audit_bundle},
            )
            return Response(
                pdf_bytes,
                mimetype="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        if fmt == "dossier-pdf":
            # The dossier is titled by its trust state (services/verification_dossier
            # .derive_trust_state); the state it was rendered from travels in the
            # response headers so a reader of the PDF alone can check the two agree.
            try:
                built = build_dossier(project_id, tree)
                pdf_bytes, engine = render_pdf(built["html"])
            except PdfRendererUnavailable as exc:
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                audit.log_audit(
                    request_id,
                    project_id,
                    "EXPORT_DOSSIER_PDF",
                    success=False,
                    duration_ms=duration_ms,
                    error_message=str(exc),
                    details={"format": fmt, "renderers": exc.detail},
                )
                return _renderer_unavailable(exc)
            except Exception as exc:
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                audit.log_exception(
                    request_id,
                    project_id,
                    "EXPORT_DOSSIER_PDF",
                    exc,
                    duration_ms=duration_ms,
                )
                return jsonify({"ok": False, "error": str(exc)}), 500
            state = built["state"]
            filename = f"{filename_base}-verification-dossier.pdf"
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            audit.log_audit(
                request_id,
                project_id,
                "EXPORT_DOSSIER_PDF",
                success=True,
                duration_ms=duration_ms,
                details={
                    "format": "dossier-pdf",
                    "filename": filename,
                    "trust_state": state.get("trust_state"),
                    "renderer": engine,
                },
            )
            return Response(
                pdf_bytes,
                mimetype="application/pdf",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "X-Assure-Trust-State": str(state.get("trust_state") or ""),
                    "X-Assure-Renderer": engine,
                },
            )

        if fmt == "audit-pdf":
            try:
                try:
                    from ..services.audit_bundle import export_audit_bundle_pdf
                except ImportError:
                    from services.audit_bundle import export_audit_bundle_pdf
                pdf_bytes = export_audit_bundle_pdf(project_id, tree)
            except PdfRendererUnavailable as exc:
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                audit.log_audit(
                    request_id,
                    project_id,
                    "EXPORT_AUDIT_PDF",
                    success=False,
                    duration_ms=duration_ms,
                    error_message=str(exc),
                    details={"format": fmt, "renderers": exc.detail},
                )
                return _renderer_unavailable(exc)
            except Exception as exc:
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                audit.log_exception(
                    request_id,
                    project_id,
                    "EXPORT_AUDIT_PDF",
                    exc,
                    duration_ms=duration_ms,
                )
                return jsonify({"ok": False, "error": str(exc)}), 500
            filename = f"{filename_base}-audit-report.pdf"
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            audit.log_audit(
                request_id,
                project_id,
                "EXPORT_AUDIT_PDF",
                success=True,
                duration_ms=duration_ms,
                details={"format": "audit-pdf", "filename": filename},
            )
            return Response(
                pdf_bytes,
                mimetype="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        if fmt in ("jdf", "bundle"):
            # The sidecar is the half of the export that used to stay behind: the
            # document tree with its per-node provenance, each node's verification
            # state, the source manifest, the version chain and the drafting model.
            # `jdf` serves it alone; `bundle` serves it with the verification dossier
            # PDF, the dossier's own state as `verification_state.json` (the same dict
            # the PDF was rendered from — `services/verification_dossier.build_dossier`
            # builds both, so they cannot disagree), the compliance audit report, and
            # a `manifest.json` naming every member with its SHA-256. When no PDF
            # engine can run on this server the PDF members are left out and the
            # manifest says so; a text dump is never shipped under a .pdf name
            # (customer QA, 2026-09-26). Every member carries the export's own name
            # — `<project>-<version>-dossier` — so a reader holding one file can tell
            # which export the others belong to.
            dossier = _dossier_base(project_id, current_document_version(project_id))
            jdf_name = f"{dossier}.jdf.json"
            manifest: dict = {}
            try:
                sidecar = build_jdf_sidecar(project_id, tree)
                sidecar_bytes = json.dumps(sidecar, indent=2, ensure_ascii=False).encode("utf-8")
                if fmt == "jdf":
                    action = "EXPORT_JDF"
                    filename = jdf_name
                    mimetype = "application/vnd.assure.jdf+json"
                    payload = sidecar_bytes
                else:
                    try:
                        from ..services.audit_bundle import export_audit_bundle_pdf
                    except ImportError:
                        from services.audit_bundle import export_audit_bundle_pdf
                    built = build_dossier(project_id, tree)
                    state = built["state"]
                    state_bytes = verification_state_json(state)
                    members: list[tuple[str, bytes]] = []
                    pdf_note: dict
                    try:
                        dossier_pdf, engine = render_pdf(built["html"])
                        audit_pdf = export_audit_bundle_pdf(project_id, tree)
                        members.append((f"{dossier}.pdf", dossier_pdf))
                        members.append((f"{dossier}-audit-report.pdf", audit_pdf))
                        pdf_note = {"included": True, "renderer": engine}
                    except PdfRendererUnavailable as exc:
                        pdf_note = {
                            "included": False,
                            "reason": "PDF renderer unavailable on this server",
                            "detail": exc.detail,
                        }
                    members.append((jdf_name, sidecar_bytes))
                    members.append(("verification_state.json", state_bytes))
                    manifest = {
                        "export": dossier,
                        "project_id": project_id,
                        "exported_at": state.get("exported_at"),
                        "trust_state": state.get("trust_state"),
                        "title": state.get("title"),
                        "status_band": state.get("status_band"),
                        "pdf": pdf_note,
                        "members": [
                            {
                                "name": name,
                                "bytes": len(data),
                                "sha256": hashlib.sha256(data).hexdigest(),
                            }
                            for name, data in members
                        ],
                    }
                    members.append(
                        ("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"))
                    )
                    action = "EXPORT_BUNDLE"
                    filename = f"{dossier}.zip"
                    mimetype = "application/zip"
                    payload = _bundle_zip(*members)
            except Exception as exc:
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                audit.log_exception(
                    request_id,
                    project_id,
                    "EXPORT_JDF",
                    exc,
                    duration_ms=duration_ms,
                    details={"format": fmt},
                )
                return jsonify({"ok": False, "error": str(exc)}), 500
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            audit.log_audit(
                request_id,
                project_id,
                action,
                success=True,
                duration_ms=duration_ms,
                details={
                    "format": fmt,
                    "filename": filename,
                    "anchors": sum(1 for node in sidecar.get("nodes") or [] if node.get("anchor")),
                    "trust_state": manifest.get("trust_state"),
                    "pdf_included": (manifest.get("pdf") or {}).get("included"),
                },
            )
            return Response(
                payload,
                mimetype=mimetype,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        if fmt != "docx":
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            audit.log_audit(
                request_id,
                project_id,
                "EXPORT_DOCX",
                success=False,
                duration_ms=duration_ms,
                error_message="Unsupported format",
                details={"format": fmt},
            )
            return jsonify(
                {
                    "error": "Unsupported format",
                    "supported": [
                        "docx",
                        "json",
                        "md",
                        "html",
                        "pdf",
                        "audit-pdf",
                        "dossier-pdf",
                        "jdf",
                        "bundle",
                    ],
                }
            ), 400

        try:
            buffer = export_jdf_to_docx(tree, include_citations=include_citations)
            filename = f"{_doc_title(tree)}.docx"
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            audit.log_audit(
                request_id,
                project_id,
                "EXPORT_DOCX",
                success=True,
                duration_ms=duration_ms,
                details={
                    "format": fmt,
                    "filename": filename,
                    "include_citations": include_citations,
                },
            )
            return Response(
                buffer.getvalue(),
                mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            audit.log_exception(
                request_id,
                project_id,
                "EXPORT_DOCX",
                exc,
                duration_ms=duration_ms,
                details={"format": fmt},
            )
            raise
