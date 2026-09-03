"""Sprint 1 adoption engine: lock inference and audit manifest export."""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime
from typing import Any

from flask import jsonify, request
from pydantic import BaseModel, Field

try:
    from ..db.audit_repository import _collect_provenance, fetch_audit_entries
    from ..db.jdf_repository import fetch_latest_jdf_or_empty, save_jdf_revision
    from ..lib.logger import get_audit_logger
    from ..services.lock_inference import document_substrate_text, infer_lock_candidates
    from ..upload_limits import UploadRejectedError, pdf_has_visual_content, validate_upload_bytes
except ImportError:
    from db.audit_repository import _collect_provenance, fetch_audit_entries
    from db.jdf_repository import fetch_latest_jdf_or_empty, save_jdf_revision
    from lib.logger import get_audit_logger
    from services.lock_inference import document_substrate_text, infer_lock_candidates
    from upload_limits import UploadRejectedError, pdf_has_visual_content, validate_upload_bytes


class InferLocksPayload(BaseModel):
    text: str | None = None
    pdf_base64: str | None = None
    filename: str | None = None
    has_visual_content: bool | None = None
    min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)


class ApplyLocksPayload(BaseModel):
    candidates: list[dict[str, Any]] = Field(default_factory=list)


def register_adoption_routes(app) -> None:
    @app.post("/api/projects/<project_id>/infer-locks")
    def infer_locks(project_id: str):
        request_id = str(uuid.uuid4())
        audit = get_audit_logger()
        data = request.get_json(silent=True) or {}
        try:
            payload = InferLocksPayload.model_validate(data)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        doc = fetch_latest_jdf_or_empty(project_id)
        substrate = (payload.text or "").strip() or document_substrate_text(doc)

        pdf_bytes: bytes | None = None
        if payload.pdf_base64:
            try:
                pdf_bytes = base64.standard_b64decode(payload.pdf_base64)
            except Exception:
                return jsonify({"ok": False, "error": "Invalid pdf_base64 payload."}), 400
            name = (payload.filename or "upload.pdf").strip() or "upload.pdf"
            try:
                validate_upload_bytes(name, pdf_bytes)
            except UploadRejectedError as exc:
                return jsonify({"ok": False, "error": str(exc)}), exc.http_status

        has_visual = payload.has_visual_content
        if has_visual is None and pdf_bytes:
            has_visual = pdf_has_visual_content(pdf_bytes)

        if not substrate.strip() and not pdf_bytes:
            return jsonify({"ok": True, "candidates": [], "message": "No substrate text available."})

        try:
            result = infer_lock_candidates(
                substrate,
                pdf_bytes=pdf_bytes,
                has_visual_content=has_visual,
            )
            candidates = result.candidates
            if payload.min_confidence > 0:
                candidates = [c for c in candidates if c.get("confidence", 0) >= payload.min_confidence]
        except RuntimeError as exc:
            audit.log_audit(
                request_id,
                project_id,
                "INFER_LOCKS",
                success=False,
                error_message=str(exc),
            )
            return jsonify({"ok": False, "error": str(exc)}), 503
        except Exception as exc:
            audit.log_audit(
                request_id,
                project_id,
                "INFER_LOCKS",
                success=False,
                error_message=str(exc),
            )
            return jsonify({"ok": False, "error": "Lock inference failed."}), 500

        audit.log_audit(
            request_id,
            project_id,
            "INFER_LOCKS",
            success=True,
            details={
                "candidate_count": len(candidates),
                "model": result.model,
                "has_visual_content": result.has_visual_content,
            },
        )
        return jsonify(
            {
                "ok": True,
                "candidates": candidates,
                "count": len(candidates),
                "model": result.model,
                "has_visual_content": result.has_visual_content,
            }
        )

    @app.post("/api/projects/<project_id>/apply-locks")
    def apply_locks(project_id: str):
        request_id = str(uuid.uuid4())
        audit = get_audit_logger()
        data = request.get_json(silent=True) or {}
        try:
            payload = ApplyLocksPayload.model_validate(data)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        if not payload.candidates:
            return jsonify({"ok": False, "error": "No candidates provided."}), 400

        doc = fetch_latest_jdf_or_empty(project_id)
        ledger = dict(doc.get("truth_ledger") or {})
        applied: list[str] = []
        for item in payload.candidates:
            key = str(item.get("canonical_key") or "").strip()
            if not key:
                continue
            try:
                ledger[key] = float(item.get("value"))
                applied.append(key)
            except (TypeError, ValueError):
                continue

        if not applied:
            return jsonify({"ok": False, "error": "No valid lock values."}), 400

        doc["truth_ledger"] = ledger
        result = save_jdf_revision(
            project_id,
            doc,
            mutation_type="LOCK_INFERENCE",
            change_summary=f"Applied {len(applied)} inferred lock(s)",
        )
        audit.log_audit(
            request_id,
            project_id,
            "APPLY_LOCKS",
            success=True,
            details={"keys": applied},
        )
        return jsonify({"ok": True, "applied": applied, "truth_ledger": ledger, **result})

    @app.get("/api/projects/<project_id>/export-audit")
    def export_audit(project_id: str):
        doc = fetch_latest_jdf_or_empty(project_id)
        entries = fetch_audit_entries(project_id)
        z3_logs = [e for e in entries if "Z3" in str(e.get("action") or "").upper()]
        redhat_logs = [
            e
            for e in entries
            if "REDHAT" in str(e.get("action") or "").upper()
            or str((e.get("details") or {}).get("task_type") or "").lower() == "redhat"
        ]
        manifest = {
            "project_id": project_id,
            "exported_at": datetime.now(UTC).isoformat(),
            "document_id": doc.get("document_id"),
            "source_files": (doc.get("meta") or {}).get("source_files") or [],
            "truth_ledger": doc.get("truth_ledger") or {},
            "z3_logs": z3_logs,
            "redhat_logs": redhat_logs,
            "audit_entries": entries,
            "node_provenance": _collect_provenance(doc.get("body") or []),
            "jdf_body": doc.get("body") or [],
        }
        return jsonify({"ok": True, "manifest": manifest})
