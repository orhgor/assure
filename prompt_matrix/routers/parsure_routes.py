"""Parsure review backend: intake reports, field decisions, disputes, export, audit.

Reads and writes the reports ``services/v1_orchestrator.run_after_parse``
saved (``db/parsure_repository``). Every route is under
``@project_ownership_required`` (routers/ingest_jobs_routes sets the
pattern). Field mutations keep the spec §5 split: a reviewer's accept or
correction sets ``field_state=accepted`` / ``routing_action=none``; a dispute
sets ``disputed`` / ``adjudicator_queue`` with ``due_at = opened_at + 72h``;
each mutation is one audit event (spec §9 item 22). Exports strip the
report's private ``_`` keys (page texts kept for re-extraction).

The project-wide export (``GET …/parsure/export``) and the two server-rendered
pages (``/parsing``, ``/parsing/<report_id>`` in ``web.py``) share the words
and formatting in the "Words" section below — one table of modality names, one
money/date formatter, one reason-to-sentence rewrite — so a value reads the
same in a cell, a CSV and a record page. They live here rather than in the
db layer because they are presentation, and here rather than in ``web.py``
because that file is a route registry and the same words are needed by the
routes. Added 2026-09-26 after the client asked where the extracted data can
be read in bulk.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import datetime
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

#: Project-wide CSV, long format: one row per document × field.
PROJECT_CSV_COLUMNS = (
    "report_id", "document_id", "filename", "document_type", "field", "label", "value",
    "extraction_confidence", "field_state", "routing_action", "review_required", "reason", "source_page", "created_at",
)

#: ``state=`` filter values for the project export and the page's state select.
#: ``needs_review`` is every field still asking for a person (routing not
#: ``none``, or disputed / rejected); ``not_found`` is a field the extractor did
#: not find (``value: null``) — it also needs a person, so the two overlap on
#: purpose: they are "show me" filters, not a partition.
STATE_FILTERS = ("needs_review", "accepted", "not_found")


# --------------------------------------------------------------------------
# Words — shared by the export, /parsing and /parsing/<report_id>
# --------------------------------------------------------------------------

MODALITY_WORDS = {
    "digital_pdf": "Digital PDF",
    "scanned_pdf": "Scan",
    "phone_photo": "Phone photo",
    "screenshot": "Screenshot",
    "handwritten": "Handwritten",
    "table_image": "Table image",
    "mixed": "Mixed bundle",
    "text": "Text",
}
MATERIAL_WORDS = {
    "pdf": "PDF",
    "image": "Image",
    "photo": "Photo",
    "screenshot": "Screenshot",
    "handwritten_image": "Handwritten note",
    "table": "Table image",
    "mixed_bundle": "Mixed bundle",
    "text_file": "Text file",
}
PARSER_WORDS = {"jdf-cli": "JDF", "jdf-cli+tesseract": "JDF + OCR", "textract": "Textract", "pymupdf": "PyMuPDF", "text": "Text"}
FLAG_WORDS = {
    "low_res": "Low resolution",
    "low_resolution": "Low resolution",
    "blurry": "Blurry",
    "low_contrast": "Low contrast",
    "no_text": "No text",
    "skewed": "Skewed",
    "glare": "Glare",
    "noisy": "Noisy",
    "faint_signature": "Faint signature",
    "handwritten": "Handwritten",
    "no_text_layer": "No text layer",
}
LOW_QUALITY_FLAGS = {"low_res", "low_resolution", "blurry", "low_contrast", "skewed", "glare", "noisy"}
STATE_WORDS = {
    "accepted": "Accepted",
    "partial": "Partial",
    "unverified": "Unverified",
    "disputed": "Disputed",
    "rejected": "Rejected",
}
ROUTING_WORDS = {
    "manual_review": "Needs a reviewer",
    "adjudicator_queue": "With an adjudicator",
    "compliance_review": "Needs compliance sign-off",
    "retry_parsure": "Retry with another reader",
    "replay_later": "Waiting for replay",
    "none": "No action",
}
REASON_LABELS = dict(repo.REASON_CATEGORIES) if hasattr(repo, "REASON_CATEGORIES") else {}
#: Audit event types in words (docs/parsure.md lists the machine names).
EVENT_WORDS = {
    "intake_received": "Received",
    "quality_assessed": "Quality assessed",
    "classified": "Type identified",
    "fields_extracted": "Fields extracted",
    "decision_applied": "Decision applied",
    "field_accepted": "Field accepted",
    "field_corrected": "Field corrected",
    "dispute_opened": "Dispute opened",
    "dispute_resolved": "Dispute resolved",
    "classification_overridden": "Type changed by reviewer",
    "exported": "Exported",
}
UNCERTAIN_TYPES = (None, "", "unknown", "uncertain", "other")


def words(value: Any, table: dict[str, str] | None = None) -> str | None:
    """``snake_case`` → "Snake case", through ``table`` when it has the key."""
    if value in (None, ""):
        return None
    key = str(value).strip().lower()
    if table and key in table:
        return table[key]
    return key.replace("_", " ").replace("-", " ").strip().capitalize()


def num(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def score_label(value: Any) -> str:
    """A 0–1 score as a percentage ("85%"); ``None`` reads "—", never a default.

    The shell shows every confidence and quality as a percentage; the intake
    page showed the same numbers as 0.85 next to 0.42, so a reader compared
    two notations for one figure (2026-09-26). One notation everywhere.
    """
    n = num(value)
    return f"{round(max(0.0, min(1.0, n)) * 100):d}%" if n is not None else "—"


def date_label(value: Any) -> str:
    """"Mar 15, 2026" from an ISO string / datetime; "—" when there is none."""
    if value in (None, ""):
        return "—"
    dt = value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value[:10]
    try:
        return f"{dt:%b} {dt.day}, {dt.year}"
    except (AttributeError, ValueError):
        return str(value)[:10]


def datetime_label(value: Any) -> str:
    """"Mar 15, 2026 · 10:04" for the history timeline."""
    if value in (None, ""):
        return "—"
    dt = value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T"))
        except ValueError:
            return value[:16]
    try:
        return f"{dt:%b} {dt.day}, {dt.year} · {dt:%H:%M}"
    except (AttributeError, ValueError):
        return str(value)[:16]


def doc_type_label(classification: Any) -> str:
    """The document type in words; anything not named reads "Type uncertain"."""
    if isinstance(classification, str):
        value = classification
    elif isinstance(classification, dict):
        value = classification.get("document_type")
    else:
        value = None
    if value in UNCERTAIN_TYPES:
        return "Type uncertain"
    return words(value) or "Type uncertain"


def document_type_key(report: dict[str, Any]) -> str:
    """The grouping key for a report: its (possibly overridden) type or ``uncertain``."""
    value = (report.get("classification") or {}).get("document_type") if isinstance(report.get("classification"), dict) else None
    return "uncertain" if value in UNCERTAIN_TYPES else str(value)


def value_label(field: dict[str, Any] | None, value: Any = None, *, raw: bool = False) -> str:
    """A field's value for a cell or a CSV.

    Money reads ``1,284.00`` — two decimals, no currency sign, because the
    extractor does not record a currency and a "$" would be a guess. Dates read
    "Aug 14, 2026". Numbers keep their own precision with thousands separators.
    ``None`` is "—" (the field was not found). ``raw=True`` skips the "—" so a
    CSV cell stays empty rather than carrying a dash.
    """
    if field is not None and value is None:
        value = field.get("value")
    if value is None:
        return "" if raw else "—"
    ftype = (field or {}).get("field_type")
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if ftype == "money" and isinstance(value, (int, float)):
        return f"{float(value):,.2f}"
    if ftype == "date" and isinstance(value, str):
        label = date_label(value)
        return label if label != "—" else value
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".") if value != int(value) else f"{int(value):,}"
    if isinstance(value, (list, tuple)):
        return ", ".join(value_label(None, v, raw=raw) for v in value)
    return str(value)


def reason_words(reason: Any) -> str:
    """The decision policy's reason string in plain words.

    The raw string stays in the API and the CSV; on the pages the system
    tokens (``extraction_confidence 0.52 < 0.75``, ``number_quality
    handwritten: …``) read as a sentence fragment a reviewer can act on
    (brief §2 "Wording clarity").
    """
    if not reason:
        return "No reason recorded"
    text = str(reason).strip()
    m = re.match(r"^(extraction|verification)_confidence\s+([0-9.]+)\s*<\s*([0-9.]+)$", text)
    if m:
        return f"{m.group(1).capitalize()} confidence {m.group(2)} is below {m.group(3)}"
    m = re.match(r"^(number|signature)_quality\s+([a-z_]+)\s*:?\s*(.*)$", text, re.I)
    if m:
        tail = f": {m.group(3).strip()}" if m.group(3).strip() else ""
        return f"{m.group(1).capitalize()} reads {m.group(2).replace('_', ' ')}{tail}"
    m = re.match(r"^z3 violation\s*:\s*(.*)$", text, re.I)
    if m:
        return f"Verification failed: {m.group(1).strip()}" if m.group(1).strip() else "Verification failed"
    m = re.match(r"^plausibility rule '([^']+)' failed\s*:?\s*(.*)$", text, re.I)
    if m:
        return f"Implausible ({m.group(1).replace('_', ' ')}){': ' + m.group(2).strip() if m.group(2).strip() else ''}"
    m = re.match(r"^disputed\s*:\s*(.*)$", text, re.I)
    if m:
        return f"Disputed: {m.group(1).strip()}"
    if text.lower() == "field not found":
        return "Not found in the document"
    if text.lower().startswith("compliance-bound"):
        return "Compliance-bound: a person must confirm it"
    text = re.sub(r"could not be parsed", "could not be read", text, flags=re.I)
    return text[0].upper() + text[1:]


def field_bucket(field: dict[str, Any]) -> str:
    """``not_found`` / ``accepted`` / ``needs_review`` — the filter a field answers to.

    A field with no value is ``not_found`` whatever its state says; an accepted
    field is ``accepted``; everything else has a value and is not accepted, so
    a person still has to look at it.
    """
    if field.get("value") is None:
        return "not_found"
    if str(field.get("field_state") or "") == "accepted":
        return "accepted"
    return "needs_review"


def field_mark(field: dict[str, Any]) -> str:
    """The quiet cell mark: ``accepted`` (plain ink), ``review`` (amber dot),
    ``rejected`` (red dot; also disputed), ``missing`` ("—")."""
    if field.get("value") is None:
        return "missing"
    state = str(field.get("field_state") or "")
    if state == "accepted":
        return "accepted"
    if state in ("rejected", "disputed"):
        return "rejected"
    return "review"


def _matches_state(field: dict[str, Any], state: str | None) -> bool:
    if not state:
        return True
    if state == "needs_review":
        return repo._needs_attention(field) or field_bucket(field) == "needs_review"
    return field_bucket(field) == state


def taxonomy_order(document_type: str) -> list[str]:
    """Field names in the ICP taxonomy's order for ``document_type``; empty for
    ``uncertain`` or a type the taxonomy does not know."""
    specs = getattr(fx, "FIELD_TAXONOMY", {}).get(document_type) or []
    return [spec.name for spec in specs]


def type_columns(document_type: str, reports: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The columns of one document-type table: taxonomy order first, then any
    field a report carries that the taxonomy does not name, in first-seen order.
    Only fields present in at least one report become columns."""
    seen: dict[str, str] = {}
    for report in reports:
        for f in report.get("fields") or []:
            name = str(f.get("name") or "")
            if name and name not in seen:
                seen[name] = str(f.get("label") or words(name) or name)
    ordered = [n for n in taxonomy_order(document_type) if n in seen]
    ordered += [n for n in seen if n not in ordered]
    return [{"name": n, "label": seen[n]} for n in ordered]


def group_reports_by_type(reports: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """(type key, reports) in taxonomy order, ``uncertain`` last; each group in
    the reports' own (newest-first) order."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for report in reports:
        groups.setdefault(document_type_key(report), []).append(report)
    known = [t for t in getattr(fx, "DOCUMENT_TYPES", ()) if t in groups]
    extra = sorted(t for t in groups if t not in known and t != "uncertain")
    order = known + extra + (["uncertain"] if "uncertain" in groups else [])
    return [(t, groups[t]) for t in order]


def export_documents(project_id: str, *, document_type: str | None = None, state: str | None = None) -> list[dict[str, Any]]:
    """Every report of the project as ``{report_id, document_id, filename,
    document_type, created_at, fields:[…]}`` with private keys stripped and the
    filters applied. A document whose fields all fall outside ``state`` is
    dropped; a document with no fields at all stays (it is data the reviewer
    should see is missing) unless a state filter is set."""
    out: list[dict[str, Any]] = []
    for report in repo.list_reports(project_id, limit=1000):
        type_key = document_type_key(report)
        if document_type and type_key != document_type:
            continue
        fields = [repo.public_report(f) if isinstance(f, dict) else f for f in (report.get("fields") or [])]
        if state:
            fields = [f for f in fields if _matches_state(f, state)]
            if not fields:
                continue
        out.append({
            "report_id": report.get("report_id"),
            "document_id": report.get("document_id"),
            "filename": report.get("filename"),
            "document_type": type_key,
            "document_type_label": doc_type_label(type_key),
            "created_at": report.get("created_at"),
            "fields": fields,
        })
    return out


def project_csv_long(documents: list[dict[str, Any]]) -> str:
    """One row per document × field (``PROJECT_CSV_COLUMNS``); values raw, not formatted."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(PROJECT_CSV_COLUMNS)
    for d in documents:
        for f in d["fields"]:
            span = f.get("source_span") or {}
            writer.writerow([
                d.get("report_id"), d.get("document_id") or "", d.get("filename") or "", d.get("document_type"),
                f.get("name"), f.get("label"), "" if f.get("value") is None else f.get("value"),
                f.get("extraction_confidence"), f.get("field_state"), f.get("routing_action"),
                "true" if f.get("review_required") else "false", f.get("reason") or "", span.get("page") or "",
                d.get("created_at") or "",
            ])
    return buf.getvalue()


def project_csv_wide(documents: list[dict[str, Any]]) -> str:
    """One row per document, one column per field, headed by the field labels.

    This is the shape a spreadsheet user asked for (2026-09-25): a table with a
    document per row. Columns are grouped by document type in taxonomy order,
    deduplicated by field name (``policy_number`` appears in several types and
    gets one column); a label two different fields share gets the field name in
    parentheses so no two columns read the same. Cells hold the value as the
    document carries it; ``needs_review`` counts the fields of that row still
    asking for a person so a filter in the spreadsheet finds them.
    """
    by_type: dict[str, list[dict[str, Any]]] = {}
    for d in documents:
        by_type.setdefault(d["document_type"], []).append(d)
    type_order = [t for t, _ in group_reports_by_type([{"classification": {"document_type": t}} for t in by_type])]
    columns: list[dict[str, str]] = []
    names: set[str] = set()
    for t in type_order:
        for col in type_columns(t, by_type[t]):
            if col["name"] not in names:
                names.add(col["name"])
                columns.append(col)
    label_counts: dict[str, int] = {}
    for col in columns:
        label_counts[col["label"]] = label_counts.get(col["label"], 0) + 1
    headers = [c["label"] if label_counts[c["label"]] == 1 else f"{c['label']} ({c['name']})" for c in columns]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["report_id", "filename", "document_type", "created_at", "needs_review", *headers])
    for t in type_order:
        for d in by_type[t]:
            by_name = {str(f.get("name")): f for f in d["fields"]}
            needs = sum(1 for f in d["fields"] if repo._needs_attention(f))
            row = [d.get("report_id"), d.get("filename") or "", d.get("document_type"), d.get("created_at") or "", needs]
            for col in columns:
                f = by_name.get(col["name"])
                row.append("" if f is None or f.get("value") is None else f.get("value"))
            writer.writerow(row)
    return buf.getvalue()


#: The one rule for "this field still asks for a person" — the pages import
#: it from here so they never grow a rule of their own.
field_needs_review = fx.field_needs_review


def type_options(current: Any = None) -> list[dict[str, Any]]:
    """The ten types in words for the record page's type selector, the current
    one marked; ``uncertain`` last as "Type uncertain"."""
    options = [{"value": t, "label": doc_type_label(t), "selected": t == current} for t in fx.DOCUMENT_TYPES]
    options.append({"value": "uncertain", "label": "Type uncertain", "selected": current in UNCERTAIN_TYPES})
    return options


def attention_counts(reports: list[dict[str, Any]]) -> dict[str, int]:
    """``parsure_repository.attention_counts`` for the page code that already imports this module."""
    return repo.attention_counts(reports)


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    review = dict(report.get("review_summary") or {})
    review.setdefault("fields_found", repo.fields_found(report))
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
        "review_summary": review,
        "fields_found": review["fields_found"],
        "nothing_extracted": repo.nothing_extracted(report),
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

    @app.get("/api/projects/<project_id>/parsure/queue")
    @project_ownership_required
    def parsure_queue(project_id: str):
        """Review queue: every field still asking for a person (spec §9 item 18).

        Newest report first; within a report overdue disputes, then disputes,
        then the rest. Each item's ``dispute`` carries ``overdue`` and the SLA
        in words so the page can say "overdue by 2 h" without its own clock.
        """
        try:
            limit = int(request.args.get("limit") or 200)
        except ValueError:
            limit = 200
        queue = repo.list_queue(project_id, limit=limit)
        return jsonify({"ok": True, "items": queue["items"], "counts": queue["counts"], "total": queue["total"], "now": queue["now"]})

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

    @app.get("/api/projects/<project_id>/parsure/export")
    @project_ownership_required
    def parsure_export_project(project_id: str):
        """Every extracted value of the project in one file (the client's
        "where can we show the parsed data in bulk?", 2026-09-25).

        ``format=csv`` is long (one row per document × field, raw values);
        ``format=csv&wide=1`` is one row per document with a column per field —
        the spreadsheet shape; ``format=json`` nests fields by name. Filters:
        ``document_type=<type>`` and ``state=needs_review|accepted|not_found``.
        One ``exported`` audit event with scope ``project`` per download.
        """
        fmt = (request.args.get("format") or "csv").strip().lower()
        if fmt not in ("json", "csv"):
            return jsonify({"ok": False, "error": "format must be json or csv."}), 400
        state = (request.args.get("state") or "").strip().lower() or None
        if state and state not in STATE_FILTERS:
            return jsonify({"ok": False, "error": f"state must be one of {', '.join(STATE_FILTERS)}."}), 400
        document_type = (request.args.get("document_type") or "").strip().lower() or None
        wide = (request.args.get("wide") or "").strip().lower() in ("1", "true", "yes")
        documents = export_documents(project_id, document_type=document_type, state=state)
        fields_n = sum(len(d["fields"]) for d in documents)
        repo.log_event(project_id, "exported", payload={
            "scope": "project", "format": fmt, "wide": wide and fmt == "csv",
            "documents": len(documents), "fields": fields_n,
            "filters": {"document_type": document_type, "state": state},
        })
        stamp = datetime.utcnow().strftime("%Y-%m-%d")
        safe_project = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(project_id))[:64] or "project"
        filename = f"parsure-{safe_project}-{stamp}.{fmt}"
        if fmt == "csv":
            payload = project_csv_wide(documents) if wide else project_csv_long(documents)
            mimetype = "text/csv; charset=utf-8"
        else:
            body = {
                "ok": True,
                "project_id": project_id,
                "exported_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                "filters": {"document_type": document_type, "state": state},
                "documents": [
                    {
                        "report_id": d["report_id"], "document_id": d["document_id"], "filename": d["filename"],
                        "document_type": d["document_type"], "created_at": d["created_at"],
                        "fields": {str(f.get("name")): f for f in d["fields"]},
                    }
                    for d in documents
                ],
            }
            payload, mimetype = json.dumps(body, ensure_ascii=False, indent=2, default=str), "application/json"
        return Response(payload, mimetype=mimetype, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

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
