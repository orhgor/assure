"""Verification Dossier: one trust state, one set of sections, rendered to PDF and JSON.

Customer QA of 2026-09-26 on an export from this module's predecessor: the PDF
was titled "Formal Verification Certificate" while the run's own JSON read
``laya.suggested_route = human_review``, ``fields_accepted 0 / fields_review 12``,
a cross-document conflict on ``insured_name`` and ``signature_quality.quality =
questionable``; the PDF said "No locked claims recorded", "No cross-run
contradictions detected", "No Red-Hat findings recorded"; and the file itself was
the text fallback of ``exporters/pdf_ast.py`` (commit 5c78b56, 2026-09-19), which
prints "no HTML-to-PDF engine is installed" on page 1 and ships anyway.

Three rules follow from that, and this module is where they are enforced:

* **One state, two artifacts.** :func:`build_verification_state` reads every
  input once — the export gate, the lock ledger, the intake reports (review
  counts, conflicts, signature, quality, replay, Laya), the Red-Hat record, the
  open disputes — and returns a JSON-serialisable dict. The HTML (hence the PDF)
  is rendered *from that dict* and the bundle's ``verification_state.json`` *is*
  that dict, so the two cannot disagree.
* **The title is derived, not declared.** :func:`derive_trust_state` maps the
  counts to ``verified`` / ``review_required`` / ``not_verified``; the word
  "Certificate" is only ever emitted in the ``verified`` state.
* **A missing renderer is an error, not a style.** :func:`render_html_to_pdf`
  uses Playwright when a browser is actually installed, else WeasyPrint, and
  raises :class:`PdfRendererUnavailable` when neither can run. Nothing here
  falls back to text.

Section 4 carries two Red-Hat passes (2026-09-26): the *draft audit*
(``tasks/redhat.py`` multipass over the compiled draft, read by
``audit_bundle.project_redhat_findings``) and the *intake graph critique*
(``services/redhat_graph``, policy ``rh-graph-v1``, recorded on each intake
report as ``report["redhat"]``). Each says "not run" on its own; a ``high``
intake finding is a review item for :func:`derive_trust_state`.
"""

from __future__ import annotations

import functools
import hashlib
import html
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

try:
    from ..db.drafts_repository import fetch_draft
    from ..db.jdf_repository import current_document_version, fetch_latest_jdf_or_empty
    from ..db.runs_repository import list_runs
    from ..exporters.text_ast import jdf_to_html
    from ..services.audit_bundle import compute_export_gate, project_redhat_findings
    from ..services.lock_metadata import lock_hash
    from ..services.macro_verify import detect_cross_run_contradictions
except ImportError:
    from db.drafts_repository import fetch_draft
    from db.jdf_repository import current_document_version, fetch_latest_jdf_or_empty
    from db.runs_repository import list_runs
    from exporters.text_ast import jdf_to_html
    from services.audit_bundle import compute_export_gate, project_redhat_findings
    from services.lock_metadata import lock_hash
    from services.macro_verify import detect_cross_run_contradictions

_log = logging.getLogger(__name__)

STATE_SCHEMA = "assure.verification_state/1"

#: Trust state → document title. "Certificate" appears in exactly one of them.
TRUST_TITLES: dict[str, str] = {
    "verified": "Verification Dossier — Verified",
    "review_required": "Verification Dossier — Review required",
    "not_verified": "Verification Dossier — Not verified",
}

TRUST_SUBTITLES: dict[str, str] = {
    "verified": (
        "Certificate of verification: the export gate passed, every eligible claim "
        "is supported by its source, every intake field is accepted, and no review "
        "item, dispute or conflict is open."
    ),
    "review_required": (
        "This export does not certify the document. The items listed below are "
        "still waiting for a person; the counts are the record's own."
    ),
    "not_verified": (
        "This export does not certify the document. The verification gate did not "
        "pass, a claim is contradicted, or nothing has been accepted yet."
    ),
}

#: Signature qualities the V1 heuristic emits (``quality_probe.assess_signature``)
#: and how the dossier names them. ``clear`` is never emitted in V1 (the
#: heuristic "cannot confirm a handwritten signature"); it is mapped for the day
#: it is.
_SIGNATURE_WORDS: dict[str, str] = {
    "questionable": "present but questionable",
    "faint": "present but faint",
    "incomplete": "present but incomplete",
    "missing": "missing",
    "stamped": "confirmed (stamp or electronic signature)",
    "clear": "confirmed",
    "unknown": "not assessed",
}


def _esc(text: Any) -> str:
    return html.escape(str(text if text is not None else ""))


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


class PdfRendererUnavailable(RuntimeError):
    """No HTML-to-PDF engine can run here. ``detail`` names each probe's answer."""

    def __init__(self, detail: dict[str, str]):
        super().__init__("PDF renderer unavailable on this server")
        self.detail = dict(detail)


@functools.lru_cache(maxsize=1)
def _probe_playwright() -> str:
    """``ok`` only when the Python package *and* a Chromium build actually launch.

    The package alone is not a renderer: the image carries ``playwright`` (it is
    in requirements.txt for the e2e suite) and no browser, and that is the
    combination that produced the customer's text dump — the launch failed after
    the import succeeded. Launching is the test (measured 0.27 s locally), and the
    answer is cached for the process: browsers are installed at build time, so it
    cannot change while the server runs. Tests replace this function on the
    module, which :func:`renderer_status` looks up at call time.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001 — any import failure is "not here"
        return f"missing: {type(exc).__name__}: {exc}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
    except Exception as exc:  # noqa: BLE001
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        return f"browser not installed: {type(exc).__name__}: {first[:200]}"
    return "ok"


def _probe_weasyprint() -> str:
    try:
        import weasyprint  # type: ignore[import-untyped]
    except Exception as exc:  # noqa: BLE001 — a missing libpango raises OSError, not ImportError
        return f"missing: {type(exc).__name__}: {exc}"
    return f"ok ({getattr(weasyprint, '__version__', 'unknown')})"


def renderer_status() -> dict[str, str]:
    """Each engine's answer, ``ok`` or why not — what the 503 and the manifest carry."""
    return {"playwright": _probe_playwright(), "weasyprint": _probe_weasyprint()}


def renderer_available() -> bool:
    return any(v.startswith("ok") for v in renderer_status().values())


def _render_playwright(html_doc: str) -> bytes:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(html_doc, wait_until="load")
            return page.pdf(format="A4", print_background=True)
        finally:
            browser.close()


def _render_weasyprint(html_doc: str) -> bytes:
    from weasyprint import HTML  # type: ignore[import-untyped]

    return HTML(string=html_doc).write_pdf()


def render_pdf(html_doc: str) -> tuple[bytes, str]:
    """``(pdf_bytes, engine)`` — Playwright if a browser is installed, else WeasyPrint.

    Raises :class:`PdfRendererUnavailable` with every probe's answer when neither
    engine renders. Never returns text dressed as a PDF.
    """
    status = renderer_status()
    detail = dict(status)
    if status["playwright"].startswith("ok"):
        try:
            return _render_playwright(html_doc), "playwright"
        except Exception as exc:  # noqa: BLE001
            detail["playwright"] = f"render failed: {type(exc).__name__}: {exc}"
            _log.warning("Playwright PDF render failed: %s", exc)
    if status["weasyprint"].startswith("ok"):
        try:
            return _render_weasyprint(html_doc), "weasyprint"
        except Exception as exc:  # noqa: BLE001
            detail["weasyprint"] = f"render failed: {type(exc).__name__}: {exc}"
            _log.warning("WeasyPrint PDF render failed: %s", exc)
    raise PdfRendererUnavailable(detail)


def render_html_to_pdf(html_doc: str) -> bytes:
    return render_pdf(html_doc)[0]


# ---------------------------------------------------------------------------
# Trust state
# ---------------------------------------------------------------------------


def derive_trust_state(
    *,
    gate_status: str,
    z3_status: str,
    claims_unsupported: int,
    accepted_total: int,
    open_review: int,
    open_disputes: int,
    conflicts: int,
    redhat_open: int,
    gate_unverified: bool = False,
    intake_redhat_high: int = 0,
) -> tuple[str, list[str]]:
    """The one rule behind the title. Returns ``(state, reasons)``.

    ``not_verified`` — the gate is ``blocked``, Z3 reports a violation, a claim is
    contradicted by its own source (``provenance_stats.unsupported``), or nothing
    at all has been accepted (no locked claim, no supported claim, no accepted
    intake field). ``review_required`` — anything is open: the gate is not
    ``pass``, a field waits for a person, a dispute or a conflict is open, a
    Red-Hat finding is open — from the draft audit (``redhat_open``) or a
    ``high`` finding of the intake graph critique (``intake_redhat_high``,
    ``services/redhat_graph``; there is no dismiss action in V1, so a high
    finding stays open until the report is re-read). ``verified`` — none of
    the above.

    ``accepted_total`` counting nothing is ``not_verified`` rather than
    ``review_required`` on purpose: the customer's run had 12 fields in review and
    0 accepted, and a document with no accepted evidence of any kind has no
    ground to stand on even after the review items are named.
    """
    reasons: list[str] = []
    status = str(gate_status or "review").lower()
    z3 = str(z3_status or "").upper()
    if status == "blocked":
        reasons.append("the export gate is blocked")
    if z3 == "VIOLATION":
        reasons.append("Z3 reports a violation")
    if int(claims_unsupported or 0) > 0:
        reasons.append(
            f"{_plural(int(claims_unsupported), 'claim is', 'claims are')} contradicted by their source"
        )
    if int(accepted_total or 0) <= 0:
        reasons.append("nothing has been accepted (no locked claim, no supported claim, no accepted field)")
    if reasons:
        return "not_verified", reasons
    if status != "pass":
        reasons.append(f"the export gate reads {status}")
    if gate_unverified:
        reasons.append("the gate marks the document unverified")
    if int(open_review or 0) > 0:
        reasons.append(f"{_plural(int(open_review), 'field needs', 'fields need')} review")
    if int(open_disputes or 0) > 0:
        reasons.append(f"{_plural(int(open_disputes), 'dispute is', 'disputes are')} open")
    if int(conflicts or 0) > 0:
        reasons.append(f"{_plural(int(conflicts), 'conflict is', 'conflicts are')} unresolved")
    if int(redhat_open or 0) > 0:
        reasons.append(f"{_plural(int(redhat_open), 'Red-Hat finding is', 'Red-Hat findings are')} open")
    if int(intake_redhat_high or 0) > 0:
        reasons.append(f"{_plural(int(intake_redhat_high), 'high Red-Hat finding', 'high Red-Hat findings')} on the intake graph")
    if reasons:
        return "review_required", reasons
    return "verified", []


# ---------------------------------------------------------------------------
# Readers — each returns a dict that says what it found or why it could not read.
# ---------------------------------------------------------------------------


def _collect_lock_ledger(draft: dict[str, Any], runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ledger: list[dict[str, Any]] = []
    seen: set[str] = set()
    for run in runs:
        for lock in run.get("extracted_locks") or []:
            h = str(lock.get("lock_hash") or lock_hash(lock))
            if h in seen:
                continue
            seen.add(h)
            ledger.append(
                {
                    "claim": f"{lock.get('canonical_key') or lock.get('metric')} = {lock.get('value')}",
                    "source_id": lock.get("source_id") or "",
                    "page": (lock.get("page_coordinates") or {}).get("page", 1),
                    "lock_hash": h,
                }
            )
    meta = draft.get("meta") or {}
    for row in meta.get("lock_ledger") or []:
        h = str(row.get("lock_hash") or "")
        if h and h not in seen:
            seen.add(h)
            ledger.append(dict(row))
    return ledger


def _read_reports(project_id: str) -> tuple[list[dict[str, Any]], str]:
    """The project's current intake reports (newest first), or ``([], why)``."""
    try:
        try:
            from ..db import parsure_repository as repo
        except ImportError:
            from db import parsure_repository as repo
        return list(repo.list_reports(project_id) or []), ""
    except Exception as exc:  # noqa: BLE001 — the dossier says "not read", never "none"
        _log.warning("intake reports not read for %s: %s", project_id, exc)
        return [], f"intake reports not read: {type(exc).__name__}: {exc}"


def _read_disputes(project_id: str) -> tuple[list[dict[str, Any]], str]:
    try:
        try:
            from ..db import parsure_repository as repo
        except ImportError:
            from db import parsure_repository as repo
        return list(repo.list_disputes(project_id, status="open") or []), ""
    except Exception as exc:  # noqa: BLE001
        _log.warning("disputes not read for %s: %s", project_id, exc)
        return [], f"disputes not read: {type(exc).__name__}: {exc}"


def _field_needs_review(field: dict[str, Any]) -> bool:
    """``field_extractor.field_needs_review`` — the one rule every count uses."""
    try:
        try:
            from ..services.field_extractor import field_needs_review
        except ImportError:
            from services.field_extractor import field_needs_review
        return bool(field_needs_review(field))
    except Exception:  # noqa: BLE001 — same rule, spelled out, if the import is unavailable
        return str(field.get("routing_action") or "none") != "none" or str(
            field.get("field_state") or ""
        ) in ("disputed", "rejected")


def _review_items(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for report in reports:
        filename = str(report.get("filename") or report.get("report_id") or "")
        for field in report.get("fields") or []:
            if not isinstance(field, dict) or not _field_needs_review(field):
                continue
            span = field.get("source_span") or {}
            confidence = field.get("extraction_confidence")
            items.append(
                {
                    "document": filename,
                    "report_id": report.get("report_id"),
                    "field": str(field.get("name") or ""),
                    "label": str(field.get("label") or field.get("name") or ""),
                    "value": field.get("value"),
                    "confidence": None if confidence is None else round(float(confidence), 3),
                    "state": str(field.get("field_state") or ""),
                    "routing": str(field.get("routing_action") or ""),
                    "reason": str(field.get("reason") or ""),
                    "page": span.get("page") if isinstance(span, dict) else None,
                }
            )
    return items


def _intake_conflicts(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cross-document conflicts as the intake recorded them, with the documents named.

    ``field_extractor.cross_document_conflicts`` writes ``values: [{report_id,
    document_id, value}]``; the filename is looked up here so the dossier can say
    which file carries which value. The same conflict is attached to every
    report of the project (``v1_orchestrator.attach_conflicts``), so it is
    de-duplicated by field and value set.
    """
    names = {str(r.get("report_id")): str(r.get("filename") or r.get("report_id") or "") for r in reports}
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for report in reports:
        for conflict in report.get("conflicts") or []:
            if not isinstance(conflict, dict):
                continue
            values = [
                {
                    "document": names.get(str(v.get("report_id")), str(v.get("document_id") or v.get("report_id") or "")),
                    "report_id": v.get("report_id"),
                    "value": v.get("value"),
                }
                for v in conflict.get("values") or []
                if isinstance(v, dict)
            ]
            key = (str(conflict.get("field") or ""), tuple(sorted(str(v["value"]) for v in values)))
            if key in seen:
                continue
            seen.add(key)
            distinct = sorted({str(v["value"]) for v in values})
            field = str(conflict.get("field") or "")
            out.append(
                {
                    "field": field,
                    "kind": str(conflict.get("kind") or "cross_document"),
                    "values": values,
                    "why": (
                        f"{field} reads {len(distinct)} different values across this project's documents "
                        f"({' vs '.join(repr(v) for v in distinct)}); a shared identifier must carry one "
                        "value per project. Whitespace, hyphen and case differences are not counted."
                    ),
                }
            )
    return out


def _signature_view(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """The signature as a first-class item: ``present but questionable — <basis>``.

    ``quality_report.signature`` is ``assess_signature``'s own dict (``present``,
    ``quality``, ``basis``, ``page``, ``review_required``); the basis string is the
    measurement and is quoted verbatim. One row per intake report; ``unresolved``
    is true when any row still needs a person.
    """
    rows: list[dict[str, Any]] = []
    for report in reports:
        qr = report.get("quality_report") if isinstance(report.get("quality_report"), dict) else {}
        sig = qr.get("signature")
        if not isinstance(sig, dict):
            sig_field = next(
                (f for f in report.get("fields") or [] if isinstance(f, dict) and f.get("field_type") == "signature"),
                None,
            )
            sig = (sig_field or {}).get("signature_quality") if sig_field else None
        if not isinstance(sig, dict):
            rows.append(
                {
                    "document": str(report.get("filename") or report.get("report_id") or ""),
                    "status": "not assessed",
                    "quality": None,
                    "present": None,
                    "basis": "the intake report carries no signature assessment",
                    "page": None,
                    "unresolved": False,
                }
            )
            continue
        quality = str(sig.get("quality") or "unknown")
        words = _SIGNATURE_WORDS.get(quality, f"present, quality {quality}")
        if sig.get("present") is False:
            words = _SIGNATURE_WORDS["missing"]
        unresolved = bool(sig.get("review_required")) or quality in ("questionable", "faint", "incomplete") or sig.get("present") is False
        rows.append(
            {
                "document": str(report.get("filename") or report.get("report_id") or ""),
                "status": words,
                "quality": quality,
                "present": sig.get("present"),
                "basis": str(sig.get("basis") or ""),
                "page": sig.get("page"),
                "unresolved": unresolved,
            }
        )
    unresolved = [r for r in rows if r["unresolved"]]
    if not rows:
        return {"status": "not_run", "reason": "no intake report; the signature check did not run", "items": [], "band": "signature not assessed"}
    band_quality = str((unresolved[0] if unresolved else rows[0]).get("quality") or "unknown")
    band = f"signature {band_quality}" if band_quality != "unknown" else "signature not assessed"
    if len(unresolved) > 1:
        band = f"signature unresolved on {len(unresolved)} documents"
    return {
        "status": "unresolved" if unresolved else "clear",
        "reason": "",
        "items": rows,
        "band": band,
    }


def _quality_view(reports: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        qr = report.get("quality_report") if isinstance(report.get("quality_report"), dict) else {}
        flags = sorted({str(f) for f in (report.get("quality_flags") or qr.get("flags") or [])})
        # The intake's own verification block (services/verification
        # .run_verification_after_parse): a real Z3 result on the parsed tree, and
        # a Red-Hat *shape* pass — "complete" there means every node carries an
        # ``annotations.redhat`` list, not that an audit ran. Measured on
        # node-check-4 (2026-09-26): the intake read Z3 PASS while the compile gate
        # read SKIPPED; both are printed, each named for what it is.
        verification = report.get("verification") if isinstance(report.get("verification"), dict) else {}
        rows.append(
            {
                "document": str(report.get("filename") or report.get("report_id") or ""),
                "document_quality_score": report.get("document_quality_score"),
                "parse_verification": {
                    "z3_status": verification.get("z3_status"),
                    "z3_violation_count": verification.get("z3_violation_count"),
                    "redhat_shape_pass": verification.get("redhat_status"),
                },
                "flags": flags,
                "no_text": "no_text" in flags,
                "text_chars": qr.get("text_chars"),
                "summary": qr.get("summary") or qr.get("quality_sentence"),
                "parser": " ".join(str(x) for x in (report.get("parser_name"), report.get("parser_version")) if x),
                "pages": [
                    {
                        "page": p.get("page"),
                        "quality_score": p.get("quality_score"),
                        "flags": list(p.get("flags") or []),
                        "ocr_confidence": p.get("ocr_confidence"),
                    }
                    for p in report.get("pages") or []
                    if isinstance(p, dict)
                ],
            }
        )
    if not rows:
        return {"status": "not_run", "reason": "no intake report; page quality was not measured", "items": []}
    return {"status": "measured", "reason": "", "items": rows}


def _replay_view(reports: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for report in reports:
        replay = report.get("replay") if isinstance(report.get("replay"), dict) else {}
        rows.append(
            {
                "document": str(report.get("filename") or report.get("report_id") or ""),
                "eligible": bool(replay.get("eligible")),
                "reasons": [str(r) for r in replay.get("reasons") or []],
                "replayed": bool(replay.get("replayed")),
            }
        )
    if not rows:
        return {"status": "not_run", "reason": "no intake report", "items": [], "eligible": 0}
    return {"status": "recorded", "reason": "", "items": rows, "eligible": sum(1 for r in rows if r["eligible"])}


def _laya_view(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for report in reports:
        laya = report.get("laya") if isinstance(report.get("laya"), dict) else None
        if laya is None:
            continue
        out.append(
            {
                "document": str(report.get("filename") or report.get("report_id") or ""),
                "suggested_route": laya.get("suggested_route"),
                "human_review": bool(laya.get("human_review")),
                "escalate": bool(laya.get("escalate")),
                "reasons": [str(r) for r in laya.get("reasons") or []],
            }
        )
    return out


def _intake_redhat_view(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """The intake graph critique (``services/redhat_graph``, policy
    ``rh-graph-v1``) as recorded on each intake report's ``redhat`` block.
    ``status`` is ``not_run`` with the reason when no report carries a block
    (no intake report, or reports saved before the critique existed),
    ``findings`` / ``clear`` otherwise; ``high`` is what the trust state
    reads. Distinct from the draft audit (``project_redhat_findings``): two
    passes, two lists, both in section 4."""
    try:
        try:
            from ..services import redhat_graph as rg
        except ImportError:
            from services import redhat_graph as rg
    except Exception as exc:  # noqa: BLE001
        return {"status": "not_run", "reason": f"critique module not importable: {type(exc).__name__}", "count": 0, "high": 0,
                "reports_run": 0, "reports_total": len(reports), "policy": None, "items": []}
    items: list[dict[str, Any]] = []
    ran = 0
    policy = None
    for r in reports:
        view = rg.findings_view(r)
        if not view.get("ran"):
            continue
        ran += 1
        policy = policy or view.get("policy")
        for f in view.get("findings") or []:
            items.append({
                "report_id": r.get("report_id"),
                "filename": r.get("filename"),
                "id": f.get("id"),
                "severity": f.get("severity"),
                "title": f.get("title"),
                "class": f.get("class"),
                "rule": f.get("rule"),
                "anchor": f.get("anchor") or {},
                "where": f.get("anchor_label"),
                "rationale": f.get("rationale"),
                "status": "open",
            })
    if not reports:
        return {"status": "not_run", "reason": "no intake report is recorded for this project", "count": 0, "high": 0,
                "reports_run": 0, "reports_total": 0, "policy": None, "items": []}
    if ran == 0:
        return {"status": "not_run", "reason": f"no critique is recorded on the {_plural(len(reports), 'intake report', 'intake reports')}",
                "count": 0, "high": 0, "reports_run": 0, "reports_total": len(reports), "policy": None, "items": []}
    high = sum(1 for i in items if i.get("severity") == "high")
    return {
        "status": "findings" if items else "clear",
        "reason": "" if ran == len(reports) else f"{ran} of {len(reports)} intake reports carry a critique",
        "count": len(items),
        "high": high,
        "reports_run": ran,
        "reports_total": len(reports),
        "policy": policy,
        "items": items,
    }


def _review_summary_totals(reports: list[dict[str, Any]]) -> dict[str, int]:
    """Sums of each report's ``review_summary``; ``fields_review`` is recounted with
    the queue's rule (``attention_counts``) so the dossier's figure is the one the
    shell and the Parsure page show."""
    totals = {"fields_total": 0, "fields_found": 0, "fields_accepted": 0, "fields_review": 0, "fields_rejected": 0, "fields_disputed": 0}
    for report in reports:
        summary = report.get("review_summary") if isinstance(report.get("review_summary"), dict) else {}
        fields = [f for f in report.get("fields") or [] if isinstance(f, dict)]
        totals["fields_total"] += int(summary.get("fields_total") if summary.get("fields_total") is not None else len(fields))
        totals["fields_found"] += int(
            summary.get("fields_found") if summary.get("fields_found") is not None else sum(1 for f in fields if f.get("value") is not None)
        )
        totals["fields_accepted"] += int(
            summary.get("fields_accepted") if summary.get("fields_accepted") is not None else sum(1 for f in fields if f.get("field_state") == "accepted")
        )
        totals["fields_rejected"] += int(
            summary.get("fields_rejected") if summary.get("fields_rejected") is not None else sum(1 for f in fields if f.get("field_state") == "rejected")
        )
        totals["fields_disputed"] += int(
            summary.get("fields_disputed") if summary.get("fields_disputed") is not None else sum(1 for f in fields if f.get("field_state") == "disputed")
        )
        totals["fields_review"] += sum(1 for f in fields if _field_needs_review(f))
    return totals


# ---------------------------------------------------------------------------
# The state
# ---------------------------------------------------------------------------


def status_band(counts: dict[str, Any], *, intake_present: bool, signature_band: str, redhat: dict[str, Any], conflicts_status: str) -> str:
    """"12 fields need review · 0 accepted · 1 conflict · signature questionable · Red-Hat: 2 findings".

    Real numbers or "not run"; never "none detected" for a check that did not run.
    """
    parts: list[str] = []
    if intake_present:
        parts.append(_plural(int(counts.get("fields_review") or 0), "field needs review", "fields need review"))
        parts.append(f"{int(counts.get('fields_accepted') or 0)} accepted")
    else:
        parts.append("no intake report")
    if conflicts_status == "not_run":
        parts.append("conflicts: not run")
    else:
        parts.append(_plural(int(counts.get("conflicts") or 0), "conflict", "conflicts"))
    parts.append(signature_band)
    if redhat.get("ran"):
        parts.append(f"Red-Hat: {_plural(int(redhat.get('count') or 0), 'finding', 'findings')}")
    else:
        parts.append("Red-Hat: not run")
    intake_rh = counts.get("redhat_intake_findings")
    if intake_rh is not None:
        high = int(counts.get("redhat_intake_high") or 0)
        parts.append(f"intake critique: {_plural(int(intake_rh), 'finding', 'findings')}" + (f" ({high} high)" if high else ""))
    else:
        parts.append("intake critique: not run")
    open_disputes = int(counts.get("disputes_open") or 0)
    if open_disputes:
        overdue = int(counts.get("disputes_overdue") or 0)
        parts.append(_plural(open_disputes, "open dispute", "open disputes") + (f" ({overdue} overdue)" if overdue else ""))
    parts.append(f"gate {counts.get('gate_status') or 'review'}")
    return " · ".join(parts)


def build_verification_state(project_id: str, tree: dict[str, Any] | None = None) -> dict[str, Any]:
    """Everything the dossier states, as data. The PDF and the JSON twin are both
    rendered from this return value — build it once per export.

    ``tree`` is the project's latest JDF revision when not given. The founder
    draft (``drafts`` table, keyed by the project id as workspace) is read for its
    lock ledger and as the document body when the project has no JDF revision,
    which is what the previous certificate rendered.
    """
    tree = tree if isinstance(tree, dict) else fetch_latest_jdf_or_empty(project_id)
    draft_row = fetch_draft(project_id) or {}
    draft = draft_row.get("content") if isinstance(draft_row.get("content"), dict) else {}

    gate = compute_export_gate(project_id, tree)
    stats = dict(gate.get("provenance_stats") or {})

    runs = list_runs(workspace_id=project_id)
    run_ids = [r["id"] for r in runs]
    ledger = _collect_lock_ledger(draft, runs)

    cross_run: dict[str, Any]
    if len(run_ids) >= 2:
        found = detect_cross_run_contradictions(run_ids)
        cross_run = {
            "status": "run",
            "reason": "",
            "runs_compared": len(run_ids),
            "items": [
                {
                    "conflict_type": c.get("conflict_type"),
                    "severity": c.get("severity"),
                    "claim_a": c.get("claim_a"),
                    "claim_b": c.get("claim_b"),
                    "why": c.get("explanation") or c.get("reason") or "",
                }
                for c in found
            ],
        }
    else:
        cross_run = {
            "status": "not_run",
            "reason": f"fewer than two runs recorded ({len(run_ids)}); the cross-run comparison did not run",
            "runs_compared": len(run_ids),
            "items": [],
        }

    redhat = project_redhat_findings(project_id, tree)
    redhat_items = [
        {
            "severity": i.get("severity"),
            "title": i.get("title"),
            "message": i.get("message"),
            "status": i.get("status"),
            "node_id": i.get("node_id"),
            "run_id": i.get("run_id"),
            "revision": i.get("revision"),
            "source": i.get("source"),
        }
        for i in redhat.get("items") or []
    ]
    redhat_open = sum(1 for i in redhat_items if str(i.get("status") or "open").lower() not in ("dismissed", "resolved", "fixed", "closed"))

    reports, reports_error = _read_reports(project_id)
    intake_present = bool(reports)
    review_items = _review_items(reports)
    totals = _review_summary_totals(reports)
    conflicts = _intake_conflicts(reports)
    signature = _signature_view(reports)
    quality = _quality_view(reports)
    replay = _replay_view(reports)
    laya = _laya_view(reports)
    intake_redhat = _intake_redhat_view(reports)

    disputes, disputes_error = _read_disputes(project_id)
    overdue = sum(1 for d in disputes if d.get("overdue"))

    if intake_present:
        conflicts_status = "run"
    else:
        conflicts_status = "not_run"

    accepted_total = len(ledger) + int(stats.get("supported") or 0) + int(totals["fields_accepted"])
    open_review = len(review_items)
    trust_state, reasons = derive_trust_state(
        gate_status=str(gate.get("gate_status") or "review"),
        z3_status=str(gate.get("z3_status") or ""),
        claims_unsupported=int(stats.get("unsupported") or 0),
        accepted_total=accepted_total,
        open_review=open_review,
        open_disputes=len(disputes),
        conflicts=len(conflicts) + len(cross_run["items"]),
        redhat_open=redhat_open,
        gate_unverified=bool(gate.get("unverified")),
        intake_redhat_high=int(intake_redhat.get("high") or 0),
    )
    if signature.get("status") == "unresolved" and trust_state == "verified":
        # A questionable signature is a review item even when the field rows do
        # not carry it (older reports); it cannot be certified over.
        trust_state, reasons = "review_required", ["the signature is unresolved"]

    counts = {
        "fields_total": totals["fields_total"],
        "fields_found": totals["fields_found"],
        "fields_accepted": totals["fields_accepted"],
        "fields_review": totals["fields_review"],
        "fields_rejected": totals["fields_rejected"],
        "fields_disputed": totals["fields_disputed"],
        "conflicts": len(conflicts),
        "cross_run_contradictions": len(cross_run["items"]),
        "redhat_findings": int(redhat.get("count") or 0) if redhat.get("ran") else None,
        "redhat_open": redhat_open if redhat.get("ran") else None,
        "redhat_intake_findings": int(intake_redhat.get("count") or 0) if intake_redhat.get("status") != "not_run" else None,
        "redhat_intake_high": int(intake_redhat.get("high") or 0) if intake_redhat.get("status") != "not_run" else None,
        "locked_claims": len(ledger),
        "claims_eligible": int(stats.get("eligible") or 0),
        "claims_anchored": int(stats.get("anchored") or 0),
        "claims_supported": int(stats.get("supported") or 0),
        "claims_unsupported": int(stats.get("unsupported") or 0),
        "disputes_open": len(disputes),
        "disputes_overdue": overdue,
        "documents": len(reports),
        "gate_status": str(gate.get("gate_status") or "review"),
        "z3_status": str(gate.get("z3_status") or "SKIPPED"),
        "accepted_total": accepted_total,
    }

    body_tree = tree if tree.get("body") else (draft if draft.get("body") else tree)
    title = str((body_tree.get("meta") or {}).get("title") or (tree.get("meta") or {}).get("title") or project_id)
    content_hash = hashlib.sha256(json.dumps(body_tree, sort_keys=True, default=str).encode()).hexdigest()
    try:
        version = int(current_document_version(project_id) or 0)
    except Exception:  # noqa: BLE001
        version = 0

    return {
        "schema": STATE_SCHEMA,
        "project_id": project_id,
        "exported_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "build_sha": (os.environ.get("ASSURE_BUILD_SHA") or "local").strip(),
        "document": {
            "title": title,
            "version": version,
            "content_hash": content_hash,
            "has_body": bool(body_tree.get("body")),
            "body_source": "jdf" if tree.get("body") else ("draft" if draft.get("body") else "none"),
        },
        "trust_state": trust_state,
        "title": TRUST_TITLES[trust_state],
        "subtitle": TRUST_SUBTITLES[trust_state],
        "status_band": status_band(
            counts,
            intake_present=intake_present,
            signature_band=signature["band"],
            redhat=redhat,
            conflicts_status=conflicts_status,
        ),
        "reasons": reasons,
        "counts": counts,
        "gate": {
            "gate_status": gate.get("gate_status"),
            "z3_status": gate.get("z3_status"),
            "unverified": bool(gate.get("unverified")),
            "unverified_reason": gate.get("unverified_reason") or "",
            "redhat_count": gate.get("redhat_count"),
            "provenance_stats": stats,
            "violations": list(gate.get("violations") or []),
        },
        "intake": {
            "present": intake_present,
            "reason": reports_error or ("" if intake_present else "no intake report is recorded for this project"),
            "documents": [
                {
                    "report_id": r.get("report_id"),
                    "filename": r.get("filename"),
                    "document_type": (r.get("classification") or {}).get("document_type") if isinstance(r.get("classification"), dict) else None,
                    "review_summary": r.get("review_summary") if isinstance(r.get("review_summary"), dict) else None,
                    "verification": r.get("verification") if isinstance(r.get("verification"), dict) else None,
                    "created_at": r.get("created_at"),
                }
                for r in reports
            ],
            "laya": laya,
        },
        "sections": {
            "review_required": {
                "status": "not_run" if not intake_present else ("open" if review_items else "clear"),
                "reason": reports_error or ("no intake report; there are no fields to review" if not intake_present else ""),
                "count": open_review,
                "items": review_items,
            },
            "conflicts": {
                "status": conflicts_status if not conflicts else "open",
                "reason": "" if intake_present else "no intake report; the cross-document check did not run",
                "count": len(conflicts),
                "items": conflicts,
                "cross_run": cross_run,
            },
            "redhat": {
                "status": ("findings" if redhat_items else "clear") if redhat.get("ran") else "not_run",
                "reason": str(redhat.get("reason") or ""),
                "count": int(redhat.get("count") or 0) if redhat.get("ran") else None,
                "open": redhat_open if redhat.get("ran") else None,
                "other_revisions": list(redhat.get("other_revisions") or []),
                "in_export": bool(redhat.get("in_export")),
                "items": redhat_items,
                "draft_label": "draft audit",
                "intake_label": "intake graph critique",
                "intake": intake_redhat,
            },
            "locked_claims": {
                "status": "recorded" if ledger else "none",
                "reason": "" if ledger else "no claim has been locked for this project",
                "count": len(ledger),
                "items": ledger,
            },
            "signature": signature,
            "disputes": {
                "status": "not_read" if disputes_error else ("open" if disputes else "clear"),
                "reason": disputes_error or ("" if disputes else "no dispute is open"),
                "count": len(disputes),
                "overdue": overdue,
                "items": [
                    {
                        "dispute_id": d.get("dispute_id"),
                        "report_id": d.get("report_id"),
                        "field": d.get("field_name"),
                        "reason": d.get("reason"),
                        "opened_at": d.get("opened_at"),
                        "due_at": d.get("due_at"),
                        "due_words": d.get("due_words"),
                        "overdue": bool(d.get("overdue")),
                        "actor": d.get("actor"),
                    }
                    for d in disputes
                ],
            },
            "quality": quality,
            "replay": replay,
        },
    }


def verification_state_json(state: dict[str, Any]) -> bytes:
    return json.dumps(state, indent=2, ensure_ascii=False, default=str).encode("utf-8")


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


def _fmt_conf(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return _esc(value)


def _fmt_score(value: Any) -> str:
    if value is None:
        return "not measured"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return _esc(value)


def _empty_row(cols: int, text: str) -> str:
    return f"<tr><td colspan='{cols}' class='empty'>{_esc(text)}</td></tr>"


def _section_review(section: dict[str, Any]) -> str:
    rows = ""
    for item in section.get("items") or []:
        rows += (
            f"<tr><td>{_esc(item.get('document'))}</td><td>{_esc(item.get('label'))}<br/><code>{_esc(item.get('field'))}</code></td>"
            f"<td>{_esc('—' if item.get('value') is None else item.get('value'))}</td>"
            f"<td>{_fmt_conf(item.get('confidence'))}</td><td>{_esc(item.get('state'))} / {_esc(item.get('routing'))}</td>"
            f"<td>{_esc(item.get('reason'))}</td><td>{_esc(item.get('page') if item.get('page') is not None else '—')}</td></tr>"
        )
    if not rows:
        if section.get("status") == "not_run":
            rows = _empty_row(7, f"Not run — {section.get('reason')}.")
        else:
            rows = _empty_row(7, "0 fields need review.")
    return (
        f"<p class='count'>{_plural(int(section.get('count') or 0), 'field needs', 'fields need')} a person.</p>"
        "<table><thead><tr><th>Document</th><th>Field</th><th>Value read</th><th>Confidence</th>"
        f"<th>State / routing</th><th>Reason</th><th>Page</th></tr></thead><tbody>{rows}</tbody></table>"
    )


def _section_conflicts(section: dict[str, Any]) -> str:
    out = ""
    items = section.get("items") or []
    if items:
        for c in items:
            values = "".join(
                f"<li><strong>{_esc(v.get('value'))}</strong> — {_esc(v.get('document'))}</li>" for v in c.get("values") or []
            )
            out += (
                f"<div class='conflict'><p><strong>{_esc(c.get('field'))}</strong> <span class='meta'>({_esc(c.get('kind'))})</span></p>"
                f"<ul>{values}</ul><p class='why'>{_esc(c.get('why'))}</p></div>"
            )
    elif section.get("status") == "not_run":
        out += f"<p class='empty'>Not run — {_esc(section.get('reason'))}.</p>"
    else:
        out += "<p class='empty'>0 cross-document conflicts recorded across the project's intake reports.</p>"
    cross = section.get("cross_run") or {}
    out += "<h3>Cross-run contradictions</h3>"
    if cross.get("items"):
        out += "<ul>" + "".join(
            f"<li><strong>{_esc(i.get('conflict_type'))}</strong> ({_esc(i.get('severity'))}): "
            f"{_esc(i.get('claim_a'))} vs {_esc(i.get('claim_b'))}"
            + (f" — {_esc(i.get('why'))}" if i.get("why") else "")
            + "</li>"
            for i in cross["items"]
        ) + "</ul>"
    elif cross.get("status") == "not_run":
        out += f"<p class='empty'>Not run — {_esc(cross.get('reason'))}.</p>"
    else:
        out += f"<p class='empty'>0 contradictions across {int(cross.get('runs_compared') or 0)} runs compared.</p>"
    return out


def _section_redhat_intake(intake: dict[str, Any]) -> str:
    """The intake graph critique's rows (``services/redhat_graph``), or why
    there are none. "0 findings" is only said when a critique ran."""
    if not intake or intake.get("status") == "not_run":
        reason = str((intake or {}).get("reason") or "no critique is recorded")
        return f"<p class='empty'>Not run — {_esc(reason[:1].upper() + reason[1:])}.</p>"
    rows = ""
    for item in intake.get("items") or []:
        where = str(item.get("where") or "")
        doc = str(item.get("filename") or item.get("report_id") or "")
        rows += (
            f"<tr><td>{_esc(item.get('severity'))}</td><td>{_esc(item.get('class'))}</td><td>{_esc(item.get('title'))}</td>"
            f"<td>{_esc(item.get('rationale'))}</td><td><code>{_esc(where)}</code>{(' · ' + _esc(doc)) if doc else ''}</td></tr>"
        )
    if not rows:
        rows = _empty_row(5, f"The intake graph critique ran on {_plural(int(intake.get('reports_run') or 0), 'report', 'reports')} and recorded 0 findings.")
    note = f"<p class='meta'>{_esc(intake.get('reason'))}.</p>" if intake.get("reason") else ""
    return (
        f"<p class='count'>Intake graph critique ({_esc(intake.get('policy') or 'rh-graph-v1')}): "
        f"{_plural(int(intake.get('count') or 0), 'finding', 'findings')}, {int(intake.get('high') or 0)} high, "
        f"over {_plural(int(intake.get('reports_run') or 0), 'intake report', 'intake reports')}.</p>"
        "<table><thead><tr><th>Severity</th><th>Class</th><th>Finding</th><th>Rationale</th><th>Where</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>{note}"
    )


def _section_redhat(section: dict[str, Any]) -> str:
    """Two passes, two blocks: the draft audit (``tasks/redhat.py`` multipass
    over the compiled draft, read by ``project_redhat_findings``) and the
    intake graph critique (``services/redhat_graph`` over each intake
    report's evidence graph). Each says "not run" on its own."""
    draft_head = "<h3>Draft audit — Red-Hat multipass over the compiled draft</h3>"
    intake_head = "<h3>Intake graph critique — rules over the intake evidence graph</h3>"
    intake_html = _section_redhat_intake(section.get("intake") or {})
    if section.get("status") == "not_run":
        reason = str(section.get("reason") or "Red-Hat has not been run for this project.")
        return f"{draft_head}<p class='empty'>Not run — {_esc(reason[:1].upper() + reason[1:])}</p>{intake_head}{intake_html}"
    rows = ""
    for item in section.get("items") or []:
        where = str(item.get("node_id") or item.get("run_id") or "")
        if item.get("revision"):
            where = f"{where} · revision {int(item['revision'])}" if where else f"revision {int(item['revision'])}"
        rows += (
            f"<tr><td>{_esc(item.get('severity'))}</td><td>{_esc(item.get('title'))}</td><td>{_esc(item.get('message'))}</td>"
            f"<td>{_esc(item.get('status'))}</td><td><code>{_esc(where)}</code></td></tr>"
        )
    if not rows:
        rows = _empty_row(5, "Red-Hat ran and recorded 0 findings.")
    note = ""
    if section.get("other_revisions") and not section.get("in_export"):
        listed = ", ".join(str(int(v)) for v in section["other_revisions"])
        note = f"<p class='meta'>Recorded on revision {_esc(listed)} of this document; the version in this export does not carry them.</p>"
    return (
        f"{draft_head}"
        f"<p class='count'>Red-Hat: {_plural(int(section.get('count') or 0), 'finding', 'findings')}, "
        f"{int(section.get('open') or 0)} open.</p>"
        "<table><thead><tr><th>Severity</th><th>Finding</th><th>Detail</th><th>Status</th><th>Where</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>{note}"
        f"{intake_head}{intake_html}"
    )


def _section_locks(section: dict[str, Any]) -> str:
    rows = ""
    for row in section.get("items") or []:
        rows += (
            f"<tr><td>{_esc(row.get('claim'))}</td><td><code>{_esc(row.get('source_id'))}</code></td>"
            f"<td>{_esc(row.get('page'))}</td><td><code>{_esc(row.get('lock_hash'))}</code></td></tr>"
        )
    if not rows:
        rows = _empty_row(4, f"0 locked claims — {section.get('reason')}.")
    return (
        "<table><thead><tr><th>Claim</th><th>Source</th><th>Page</th><th>Lock hash</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _section_signature(section: dict[str, Any]) -> str:
    if section.get("status") == "not_run":
        return f"<p class='empty'>Not run — {_esc(section.get('reason'))}.</p>"
    rows = ""
    for item in section.get("items") or []:
        flag = "<span class='flag'>unresolved</span>" if item.get("unresolved") else ""
        rows += (
            f"<tr><td>{_esc(item.get('document'))}</td><td><strong>{_esc(item.get('status'))}</strong> {flag}</td>"
            f"<td>{_esc(item.get('page') if item.get('page') is not None else '—')}</td><td>{_esc(item.get('basis'))}</td></tr>"
        )
    return (
        "<table><thead><tr><th>Document</th><th>Signature</th><th>Page</th><th>Measurement (verbatim)</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _section_disputes(section: dict[str, Any]) -> str:
    rows = ""
    for d in section.get("items") or []:
        due = str(d.get("due_words") or d.get("due_at") or "")
        rows += (
            f"<tr><td>{_esc(d.get('field'))}</td><td>{_esc(d.get('reason'))}</td><td>{_esc(d.get('opened_at'))}</td>"
            f"<td>{'<span class=flag>' if d.get('overdue') else ''}{_esc(due)}{'</span>' if d.get('overdue') else ''}</td>"
            f"<td>{_esc(d.get('actor'))}</td></tr>"
        )
    if not rows:
        text = f"Not read — {section.get('reason')}." if section.get("status") == "not_read" else "0 open disputes."
        rows = _empty_row(5, text)
    return (
        f"<p class='count'>{_plural(int(section.get('count') or 0), 'open dispute', 'open disputes')}, "
        f"{int(section.get('overdue') or 0)} overdue.</p>"
        "<table><thead><tr><th>Field</th><th>Reason</th><th>Opened</th><th>Due</th><th>Actor</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _section_quality(section: dict[str, Any]) -> str:
    if section.get("status") == "not_run":
        return f"<p class='empty'>Not run — {_esc(section.get('reason'))}.</p>"
    out = ""
    for item in section.get("items") or []:
        flags = ", ".join(item.get("flags") or []) or "none recorded"
        pages = "".join(
            f"<tr><td>{_esc(p.get('page'))}</td><td>{_fmt_score(p.get('quality_score'))}</td>"
            f"<td>{_esc(', '.join(p.get('flags') or []) or '—')}</td>"
            f"<td>{_fmt_conf(p.get('ocr_confidence')) if p.get('ocr_confidence') is not None else 'not measured'}</td></tr>"
            for p in item.get("pages") or []
        ) or _empty_row(4, "no page record")
        no_text = " <span class='flag'>no_text</span>" if item.get("no_text") else ""
        chars = item.get("text_chars")
        pv = item.get("parse_verification") or {}
        if pv.get("z3_status"):
            violations = pv.get("z3_violation_count")
            parse_line = (
                f"<p class='meta'>Parse-time verification of this document: Z3 {_esc(pv.get('z3_status'))}"
                + (f" ({int(violations)} violation{'s' if int(violations) != 1 else ''})" if isinstance(violations, (int, float)) else "")
                + (
                    f" · Red-Hat annotation pass {_esc(pv.get('redhat_shape_pass'))} — a shape pass, not an audit; "
                    "the Red-Hat audit is reported in section 4"
                    if pv.get("redhat_shape_pass")
                    else ""
                )
                + ".</p>"
            )
        else:
            parse_line = "<p class='meta'>Parse-time verification: not recorded on this intake report.</p>"
        out += (
            f"<h3>{_esc(item.get('document'))}</h3>"
            f"<p>Document quality {_fmt_score(item.get('document_quality_score'))} · flags: {_esc(flags)}{no_text}"
            + (f" · {int(chars):,} characters of text" if isinstance(chars, (int, float)) else "")
            + (f" · parser {_esc(item.get('parser'))}" if item.get("parser") else "")
            + "</p>"
            + (f"<p class='meta'>{_esc(item.get('summary'))}</p>" if item.get("summary") else "")
            + parse_line
            + "<table><thead><tr><th>Page</th><th>Quality</th><th>Flags</th><th>OCR confidence</th></tr></thead>"
            f"<tbody>{pages}</tbody></table>"
        )
    return out


def _section_replay(section: dict[str, Any]) -> str:
    if section.get("status") == "not_run":
        return f"<p class='empty'>Not run — {_esc(section.get('reason'))}.</p>"
    rows = ""
    for item in section.get("items") or []:
        reasons = "; ".join(item.get("reasons") or []) or "—"
        rows += (
            f"<tr><td>{_esc(item.get('document'))}</td><td>{'eligible' if item.get('eligible') else 'not eligible'}</td>"
            f"<td>{_esc(reasons)}</td><td>{'yes' if item.get('replayed') else 'no (V1 records eligibility only)'}</td></tr>"
        )
    return (
        f"<p class='count'>{_plural(int(section.get('eligible') or 0), 'document is', 'documents are')} eligible for replay.</p>"
        "<table><thead><tr><th>Document</th><th>Replay</th><th>Reasons</th><th>Replayed</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _section_laya(intake: dict[str, Any]) -> str:
    rows = ""
    for item in intake.get("laya") or []:
        rows += (
            f"<tr><td>{_esc(item.get('document'))}</td><td><strong>{_esc(item.get('suggested_route'))}</strong></td>"
            f"<td>{'yes' if item.get('human_review') else 'no'}</td><td>{'yes' if item.get('escalate') else 'no'}</td>"
            f"<td>{_esc('; '.join(item.get('reasons') or []))}</td></tr>"
        )
    if not rows:
        return ""
    return (
        "<h3>Laya triage (rules-v1)</h3>"
        "<table><thead><tr><th>Document</th><th>Suggested route</th><th>Human review</th><th>Escalate</th><th>Reasons</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


_CSS = """
body { font-family: "DejaVu Serif", Georgia, serif; margin: 2cm; color: #111; line-height: 1.45; font-size: 10.5pt; }
h1 { border-bottom: 2px solid #333; padding-bottom: 0.4em; margin-bottom: 0.2em; }
h2 { color: #333; margin-top: 1.4em; border-bottom: 1px solid #ddd; padding-bottom: 0.2em; }
h3 { color: #444; margin-top: 1em; font-size: 1em; }
table { border-collapse: collapse; width: 100%; margin: 0.6em 0; page-break-inside: auto; }
th, td { border: 1px solid #ccc; padding: 0.3em 0.5em; text-align: left; font-size: 0.88em; vertical-align: top; }
th { background: #f3f4f6; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 0.85em; }
.meta { color: #555; font-size: 0.92em; }
.subtitle { font-size: 1.05em; margin: 0.2em 0 0.6em; }
.band { border: 2px solid #333; padding: 0.6em 0.9em; font-size: 1.05em; margin: 0.6em 0 1em; background: #fafafa; }
.band.verified { border-color: #1a7f37; background: #f0fff4; }
.band.review_required { border-color: #b45309; background: #fffbeb; }
.band.not_verified { border-color: #b91c1c; background: #fef2f2; }
.reasons li { margin: 0.1em 0; }
.count { font-weight: bold; margin: 0.4em 0 0.2em; }
.empty { color: #444; font-style: italic; }
.flag { color: #b91c1c; font-weight: bold; }
.conflict { border-left: 3px solid #b91c1c; padding-left: 0.8em; margin: 0.6em 0; }
.why { color: #333; font-size: 0.92em; }
.doc { margin-top: 1em; }
"""


def build_verification_dossier_html(state: dict[str, Any], tree: dict[str, Any] | None = None) -> str:
    """The dossier's HTML, rendered from ``state`` and nothing else (plus the
    document body, which is content rather than a claim about it)."""
    sections = state.get("sections") or {}
    counts = state.get("counts") or {}
    doc = state.get("document") or {}
    trust = str(state.get("trust_state") or "not_verified")
    reasons_html = "".join(f"<li>{_esc(r)}</li>" for r in state.get("reasons") or [])
    reasons_block = f"<ul class='reasons'>{reasons_html}</ul>" if reasons_html else ""

    body_html = "<p class='empty'>No document body recorded.</p>"
    if tree is not None and tree.get("body"):
        body_html = jdf_to_html(tree)

    gate = state.get("gate") or {}
    stats = gate.get("provenance_stats") or {}
    summary_rows = [
        ("Trust state", trust.replace("_", " ")),
        ("Export gate", f"{gate.get('gate_status')} · Z3 {gate.get('z3_status')}"),
        ("Claims", f"{int(stats.get('supported') or 0)} supported · {int(stats.get('unsupported') or 0)} contradicted · {int(stats.get('anchored') or 0)} of {int(stats.get('eligible') or 0)} anchored"),
        ("Locked claims", str(counts.get("locked_claims", 0))),
        (
            "Intake fields",
            f"{counts.get('fields_review', 0)} need review · {counts.get('fields_accepted', 0)} accepted · "
            f"{counts.get('fields_found', 0)} found of {counts.get('fields_total', 0)} · {counts.get('fields_rejected', 0)} rejected · "
            f"{counts.get('fields_disputed', 0)} disputed"
            if (state.get("intake") or {}).get("present")
            else "no intake report",
        ),
        ("Conflicts", f"{counts.get('conflicts', 0)} cross-document · {counts.get('cross_run_contradictions', 0)} cross-run"),
        ("Red-Hat", (f"draft audit: {counts.get('redhat_findings')} findings ({counts.get('redhat_open')} open)" if counts.get("redhat_findings") is not None else "draft audit: not run")
                    + " · " + (f"intake graph critique: {counts.get('redhat_intake_findings')} findings ({counts.get('redhat_intake_high')} high)" if counts.get("redhat_intake_findings") is not None else "intake graph critique: not run")),
        ("Disputes", f"{counts.get('disputes_open', 0)} open · {counts.get('disputes_overdue', 0)} overdue"),
        ("Documents in intake", str(counts.get("documents", 0))),
    ]
    summary_html = "".join(f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>" for k, v in summary_rows)
    unverified_reason = gate.get("unverified_reason") or ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>{_esc(state.get('title'))} — {_esc(doc.get('title'))}</title>
<style>{_CSS}</style>
</head>
<body>
<h1>{_esc(state.get('title'))}</h1>
<p class="subtitle">{_esc(state.get('subtitle'))}</p>
<div class="band {_esc(trust)}">{_esc(state.get('status_band'))}</div>
{reasons_block}
<p class="meta">Project: {_esc(state.get('project_id'))} · Document: {_esc(doc.get('title'))} (revision {_esc(doc.get('version'))}) · Exported {_esc(state.get('exported_at'))} · Build {_esc(state.get('build_sha'))}</p>
<p class="meta">Content hash (SHA-256 of the exported document): <code>{_esc(doc.get('content_hash'))}</code></p>

<h2>1. Summary</h2>
<table><tbody>{summary_html}</tbody></table>
{f"<p class='meta'>{_esc(unverified_reason)}</p>" if unverified_reason else ""}
{_section_laya(state.get('intake') or {})}

<h2>2. Review required</h2>
{_section_review(sections.get('review_required') or {})}

<h2>3. Conflicts</h2>
{_section_conflicts(sections.get('conflicts') or {})}

<h2>4. Red-Hat findings</h2>
{_section_redhat(sections.get('redhat') or {})}

<h2>5. Locked claims</h2>
{_section_locks(sections.get('locked_claims') or {})}

<h2>6. Signature</h2>
{_section_signature(sections.get('signature') or {})}

<h2>7. Disputes</h2>
{_section_disputes(sections.get('disputes') or {})}

<h2>8. Quality</h2>
{_section_quality(sections.get('quality') or {})}

<h2>9. Replay eligibility</h2>
{_section_replay(sections.get('replay') or {})}

<h2>10. Document</h2>
<div class="doc">{body_html}</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def build_dossier(project_id: str, tree: dict[str, Any] | None = None) -> dict[str, Any]:
    """``{"state": ..., "html": ..., "tree": ...}`` — one build, both artifacts."""
    tree = tree if isinstance(tree, dict) else fetch_latest_jdf_or_empty(project_id)
    state = build_verification_state(project_id, tree)
    body_tree: dict[str, Any] | None = tree
    if not tree.get("body"):
        draft_row = fetch_draft(project_id) or {}
        draft = draft_row.get("content") if isinstance(draft_row.get("content"), dict) else None
        if draft and draft.get("body"):
            body_tree = draft
    return {"state": state, "html": build_verification_dossier_html(state, body_tree), "tree": tree}


def build_verification_certificate_html(workspace_id: str) -> str:
    """Kept for callers of the old name; the document it returns is the dossier
    (titled by trust state), not a certificate."""
    return build_dossier(workspace_id)["html"]


def export_verification_dossier(project_id: str, tree: dict[str, Any] | None = None) -> tuple[bytes, dict[str, Any], str]:
    """``(pdf_bytes, state, engine)``; raises :class:`PdfRendererUnavailable`."""
    built = build_dossier(project_id, tree)
    pdf, engine = render_pdf(built["html"])
    return pdf, built["state"], engine


def export_verification_dossier_pdf(workspace_id: str) -> bytes:
    return export_verification_dossier(workspace_id)[0]
