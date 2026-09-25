"""Parsure review backend: intake reports, field decisions, disputes, export, audit.

Reads and writes the reports ``services/v1_orchestrator.run_after_parse``
saved (``db/parsure_repository``). Every route is under
``@project_ownership_required`` (routers/ingest_jobs_routes sets the
pattern). Field mutations keep the spec §5 split: a reviewer's accept or
correction sets ``field_state=accepted`` / ``routing_action=none``; a dispute
sets ``disputed`` / ``adjudicator_queue`` with ``due_at = opened_at + 72h``;
each mutation is one audit event (spec §9 item 22). Exports strip the
report's private ``_`` keys (page texts kept for re-extraction).
"""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any

from flask import Response, jsonify, request

try:
    from ..db import parsure_repository as repo
    from ..middleware import project_ownership_required
    from ..services import field_extractor as fx
    from ..services import v1_orchestrator as orch
except ImportError:
    from db import parsure_repository as repo  # type: ignore
    from middleware import project_ownership_required  # type: ignore
    from services import field_extractor as fx  # type: ignore
    from services import v1_orchestrator as orch  # type: ignore

log = logging.getLogger(__name__)

_NOT_FOUND = "Report not found."
_NO_REPORT_YET = "No intake report yet."

CSV_COLUMNS = (
    "name", "label", "value", "extraction_confidence", "confidence_basis", "verification_confidence",
    "field_state", "routing_action", "review_required", "reason", "source_page",
)


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "report_id": report.get("report_id"),
        "document_id": report.get("document_id"),
        "revision_id": report.get("revision_id"),
        "job_id": report.get("job_id"),
        "filename": report.get("filename"),
        "document_type": (report.get("classification") or {}).get("document_type"),
        "classification_confidence": (report.get("classification") or {}).get("confidence"),
        "material_type": report.get("material_type"),
        "modality": report.get("modality"),
        "parser_name": report.get("parser_name"),
        "page_count": report.get("page_count"),
        "document_quality_score": report.get("document_quality_score"),
        "review_summary": report.get("review_summary"),
        "quality_summary": (report.get("quality_report") or {}).get("summary"),
        "replay_eligible": bool((report.get("replay") or {}).get("eligible")),
        "conflicts": len(report.get("conflicts") or []),
        "created_at": report.get("created_at"),
        "updated_at": report.get("updated_at"),
    }


def _field(report: dict[str, Any], field_name: str) -> dict[str, Any] | None:
    for f in report.get("fields") or []:
        if f.get("name") == field_name:
            return f
    return None


def _body() -> dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _actor(body: dict[str, Any]) -> str | None:
    actor = body.get("actor")
    if isinstance(actor, str) and actor.strip():
        return actor.strip()[:120]
    try:
        from flask import session

        user = session.get("user_id") or session.get("email")
        return str(user)[:120] if user else None
    except Exception:
        return None


def _coerce_value(field: dict[str, Any], value: Any) -> Any:
    """A corrected value in the field's own type; strings stay strings."""
    ftype = field.get("field_type")
    if value is None or isinstance(value, (int, float)) and ftype in ("money", "number"):
        return value
    text = str(value).strip()
    if ftype == "money":
        parsed = fx._parse_money(text)
        return parsed if parsed is not None else text
    if ftype == "number":
        parsed = fx._parse_number(text)
        return parsed if parsed is not None else text
    if ftype == "date":
        parsed = fx._parse_date(text)
        return parsed if parsed is not None else text
    if ftype == "vin":
        return text.upper()
    return text


def _csv(report: dict[str, Any]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for f in report.get("fields") or []:
        span = f.get("source_span") or {}
        writer.writerow([
            f.get("name"), f.get("label"),
            "" if f.get("value") is None else f.get("value"),
            f.get("extraction_confidence"), f.get("confidence_basis"), f.get("verification_confidence"),
            f.get("field_state"), f.get("routing_action"), "true" if f.get("review_required") else "false",
            f.get("reason") or "", span.get("page") or "",
        ])
    return buf.getvalue()


def register_parsure_routes(app) -> None:
    @app.get("/api/projects/<project_id>/parsure")
    @project_ownership_required
    def parsure_list(project_id: str):
        try:
            limit = int(request.args.get("limit") or 100)
        except ValueError:
            limit = 100
        reports = repo.list_reports(project_id, limit=limit)
        return jsonify({"ok": True, "reports": [_summary(r) for r in reports], "analytics": repo.analytics(project_id)})

    @app.get("/api/projects/<project_id>/parsure/latest")
    @project_ownership_required
    def parsure_latest(project_id: str):
        report = repo.get_latest_report(project_id)
        if not report:
            return jsonify({"ok": False, "error": _NO_REPORT_YET}), 404
        return jsonify({"ok": True, "report": repo.public_report(report)})

    @app.get("/api/projects/<project_id>/parsure/analytics")
    @project_ownership_required
    def parsure_analytics(project_id: str):
        return jsonify({"ok": True, "analytics": repo.analytics(project_id)})

    @app.get("/api/projects/<project_id>/parsure/audit-log")
    @project_ownership_required
    def parsure_audit_log(project_id: str):
        report_id = (request.args.get("report_id") or "").strip() or None
        try:
            limit = int(request.args.get("limit") or 200)
        except ValueError:
            limit = 200
        events = repo.list_events(project_id, report_id=report_id, limit=limit)
        return jsonify({"ok": True, "events": events, "event_types": list(repo.EVENT_TYPES)})

    @app.get("/api/projects/<project_id>/parsure/<report_id>")
    @project_ownership_required
    def parsure_get(project_id: str, report_id: str):
        report = repo.get_report(project_id, report_id)
        if not report:
            return jsonify({"ok": False, "error": _NOT_FOUND}), 404
        return jsonify({
            "ok": True,
            "report": repo.public_report(report),
            "corrections": repo.list_corrections(project_id, report_id),
            "disputes": repo.list_disputes(project_id, report_id=report_id),
        })

    @app.post("/api/projects/<project_id>/parsure/<report_id>/fields/<field_name>/accept")
    @project_ownership_required
    def parsure_accept(project_id: str, report_id: str, field_name: str):
        report = repo.get_report(project_id, report_id)
        if not report:
            return jsonify({"ok": False, "error": _NOT_FOUND}), 404
        field = _field(report, field_name)
        if field is None:
            return jsonify({"ok": False, "error": f"Unknown field '{field_name}'."}), 404
        if field.get("value") is None:
            return jsonify({"ok": False, "error": "A field with no extracted value cannot be accepted; correct it with a value instead."}), 409
        body = _body()
        actor = _actor(body)
        previous = {"field_state": field.get("field_state"), "routing_action": field.get("routing_action")}
        field["field_state"], field["routing_action"] = "accepted", "none"
        field["review_required"] = False
        field["accepted_by"] = actor
        field["reason"] = body.get("reason") or "accepted by reviewer"
        orch.refresh_report(report)
        repo.update_report(project_id, report_id, report)
        repo.log_event(project_id, "field_accepted", report_id=report_id, field_name=field_name, actor=actor,
                       payload={"previous": previous, "value": field.get("value")})
        return jsonify({"ok": True, "field": field, "review_summary": report["review_summary"]})

    @app.post("/api/projects/<project_id>/parsure/<report_id>/fields/<field_name>/correct")
    @project_ownership_required
    def parsure_correct(project_id: str, report_id: str, field_name: str):
        report = repo.get_report(project_id, report_id)
        if not report:
            return jsonify({"ok": False, "error": _NOT_FOUND}), 404
        field = _field(report, field_name)
        if field is None:
            return jsonify({"ok": False, "error": f"Unknown field '{field_name}'."}), 404
        body = _body()
        if "value" not in body:
            return jsonify({"ok": False, "error": "Body must include 'value'."}), 400
        actor = _actor(body)
        reason = str(body.get("reason") or "").strip() or None
        original = field.get("value")
        new_value = _coerce_value(field, body.get("value"))
        if field.get("field_type") == "vin" and isinstance(new_value, str):
            check = fx.validate_vin(new_value)
            if not check["valid"]:
                return jsonify({"ok": False, "error": f"Corrected VIN rejected: {check['reason']}"}), 400
            field["vin_check"] = check
        correction_id = repo.record_correction(
            project_id, report_id, field_name, original_value=original, corrected_value=new_value, actor=actor, reason=reason,
        )
        field["value"] = new_value
        field["raw"] = str(body.get("value")) if body.get("value") is not None else None
        field["corrected"] = True
        field["field_state"], field["routing_action"] = "accepted", "none"
        field["review_required"] = False
        field["z3_violation"], field["plausibility_violation"] = False, False
        field["reason"] = f"corrected by reviewer{': ' + reason if reason else ''}"
        field["correction_id"] = correction_id
        orch.refresh_report(report)
        repo.update_report(project_id, report_id, report)
        repo.log_event(project_id, "field_corrected", report_id=report_id, field_name=field_name, actor=actor,
                       payload={"original_value": original, "corrected_value": new_value, "reason": reason, "correction_id": correction_id})
        return jsonify({"ok": True, "field": field, "review_summary": report["review_summary"], "replay": report["replay"]})

    @app.post("/api/projects/<project_id>/parsure/<report_id>/fields/<field_name>/dispute")
    @project_ownership_required
    def parsure_dispute(project_id: str, report_id: str, field_name: str):
        report = repo.get_report(project_id, report_id)
        if not report:
            return jsonify({"ok": False, "error": _NOT_FOUND}), 404
        field = _field(report, field_name)
        if field is None:
            return jsonify({"ok": False, "error": f"Unknown field '{field_name}'."}), 404
        body = _body()
        actor = _actor(body)
        reason = str(body.get("reason") or "").strip()
        if not reason:
            return jsonify({"ok": False, "error": "Body must include a non-empty 'reason'."}), 400
        if repo.list_disputes(project_id, report_id=report_id, status="open", field_name=field_name):
            return jsonify({"ok": False, "error": "This field already has an open dispute."}), 409
        dispute = repo.open_dispute(project_id, report_id, field_name, reason=reason, actor=actor)
        field["field_state"], field["routing_action"] = "disputed", "adjudicator_queue"
        field["review_required"] = True
        field["dispute_id"] = dispute["dispute_id"]
        field["reason"] = f"disputed: {reason}"
        orch.refresh_report(report)
        repo.update_report(project_id, report_id, report)
        repo.log_event(project_id, "dispute_opened", report_id=report_id, field_name=field_name, actor=actor,
                       payload={"dispute_id": dispute["dispute_id"], "reason": reason, "due_at": dispute["due_at"]})
        return jsonify({"ok": True, "dispute": dispute, "field": field}), 201

    @app.post("/api/projects/<project_id>/parsure/<report_id>/disputes/<dispute_id>/resolve")
    @project_ownership_required
    def parsure_resolve(project_id: str, report_id: str, dispute_id: str):
        report = repo.get_report(project_id, report_id)
        if not report:
            return jsonify({"ok": False, "error": _NOT_FOUND}), 404
        dispute = repo.get_dispute(project_id, dispute_id)
        if not dispute or dispute.get("report_id") != report_id:
            return jsonify({"ok": False, "error": "Dispute not found."}), 404
        if dispute.get("status") != "open":
            return jsonify({"ok": False, "error": "Dispute is already resolved."}), 409
        body = _body()
        actor = _actor(body)
        resolution = str(body.get("resolution") or "").strip()
        if not resolution:
            return jsonify({"ok": False, "error": "Body must include a non-empty 'resolution'."}), 400
        field = _field(report, dispute["field_name"])
        resolved = repo.resolve_dispute(project_id, dispute_id, resolution=resolution, actor=actor)
        if field is not None:
            if body.get("value") is not None:
                original = field.get("value")
                new_value = _coerce_value(field, body.get("value"))
                correction_id = repo.record_correction(
                    project_id, report_id, field["name"], original_value=original, corrected_value=new_value,
                    actor=actor, reason=f"dispute {dispute_id} resolved: {resolution}",
                )
                field["value"], field["raw"], field["corrected"] = new_value, str(body.get("value")), True
                field["correction_id"] = correction_id
                field["field_state"], field["routing_action"] = "accepted", "none"
                field["review_required"] = False
                field["z3_violation"], field["plausibility_violation"] = False, False
                field["reason"] = f"dispute resolved with corrected value: {resolution}"
            else:
                # Adjudicator rejected the field without a replacement: the
                # semantic result is rejected; nothing further is routed.
                field["field_state"], field["routing_action"] = "rejected", "none"
                field["review_required"] = False
                field["reason"] = f"rejected by adjudicator: {resolution}"
            field["dispute_id"] = None
            orch.refresh_report(report)
            repo.update_report(project_id, report_id, report)
        repo.log_event(project_id, "dispute_resolved", report_id=report_id, field_name=dispute["field_name"], actor=actor,
                       payload={"dispute_id": dispute_id, "resolution": resolution, "value": body.get("value")})
        return jsonify({"ok": True, "dispute": resolved, "field": field})

    @app.get("/api/projects/<project_id>/parsure/<report_id>/fields/<field_name>/history")
    @project_ownership_required
    def parsure_field_history(project_id: str, report_id: str, field_name: str):
        report = repo.get_report(project_id, report_id)
        if not report:
            return jsonify({"ok": False, "error": _NOT_FOUND}), 404
        field = _field(report, field_name)
        if field is None:
            return jsonify({"ok": False, "error": f"Unknown field '{field_name}'."}), 404
        corrections = repo.list_corrections(project_id, report_id, field_name)
        original_value = corrections[0]["original_value"] if corrections else field.get("value")
        return jsonify({
            "ok": True,
            "field": field,
            "original_value": original_value,
            "corrections": corrections,
            "disputes": repo.list_disputes(project_id, report_id=report_id, field_name=field_name),
            "events": repo.list_events(project_id, report_id=report_id, field_name=field_name),
        })

    @app.post("/api/projects/<project_id>/parsure/<report_id>/classification")
    @project_ownership_required
    def parsure_override_classification(project_id: str, report_id: str):
        report = repo.get_report(project_id, report_id)
        if not report:
            return jsonify({"ok": False, "error": _NOT_FOUND}), 404
        body = _body()
        new_type = str(body.get("document_type") or "").strip()
        if new_type not in fx.DOCUMENT_TYPES and new_type != "uncertain":
            return jsonify({"ok": False, "error": f"document_type must be one of {', '.join(fx.DOCUMENT_TYPES)} or 'uncertain'."}), 400
        actor = _actor(body)
        reason = str(body.get("reason") or "").strip() or None
        classification = report.setdefault("classification", {})
        previous = classification.get("document_type")
        classification["override"] = {
            "document_type": new_type, "previous": previous, "reason": reason, "actor": actor, "at": orch._now(),
            "detected": {k: classification.get(k) for k in ("confidence", "basis", "matched_keywords")},
        }
        classification["document_type"] = new_type
        reextracted = bool(report.get("_page_texts"))
        if reextracted:
            orch.reextract_for_type(report, new_type)
        repo.update_report(project_id, report_id, report)
        repo.log_event(project_id, "classification_overridden", report_id=report_id, actor=actor,
                       payload={"previous": previous, "document_type": new_type, "reason": reason, "reextracted": reextracted})
        return jsonify({"ok": True, "report": repo.public_report(report), "reextracted": reextracted})

    @app.get("/api/projects/<project_id>/parsure/<report_id>/export")
    @project_ownership_required
    def parsure_export(project_id: str, report_id: str):
        report = repo.get_report(project_id, report_id)
        if not report:
            return jsonify({"ok": False, "error": _NOT_FOUND}), 404
        fmt = (request.args.get("format") or "json").strip().lower()
        if fmt not in ("json", "csv"):
            return jsonify({"ok": False, "error": "format must be json or csv."}), 400
        public = repo.public_report(report)
        repo.log_event(project_id, "exported", report_id=report_id, payload={"format": fmt, "fields": len(public.get("fields") or [])})
        filename = f"parsure-{report_id}.{fmt}"
        if fmt == "csv":
            payload, mimetype = _csv(public), "text/csv; charset=utf-8"
        else:
            payload, mimetype = json.dumps(public, ensure_ascii=False, indent=2, default=str), "application/json"
        return Response(payload, mimetype=mimetype, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
