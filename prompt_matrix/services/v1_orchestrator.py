"""Parsure V1 orchestrator: turn one finished parse into a saved intake report.

``run_after_parse`` is called by the ingest pipeline (``services/pdf_ingest``)
once the revision is saved. It consumes what the pipeline already produced —
the parse bundle, the verification result, the intake router's material /
visual / Laya output — and builds the versioned multimodal contract (spec
§4) that ``routers/parsure_routes`` serves and the review UI renders. It
never re-parses, never re-verifies and never routes: those belong to
``parser_router``, ``verification`` and the intake router (spec §8 items 1,
2, 8). Per-page quality is scored with ``quality_probe.page_quality_score``
from the signals at hand; when that module is absent the page score is
``None`` and the basis says so.

Failure isolation: the pipeline treats the return value as advisory. Any
exception here is logged and turns into ``None`` so a review-backend bug can
never fail an ingest that already has its revision.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

try:
    from ..services import field_extractor as fx
except ImportError:
    from services import field_extractor as fx  # type: ignore

log = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"
POLICY_VERSION = "v1"
#: Wiring date of the post-parse verification hook when services/verification
#: exposes no version constant of its own.
VERIFICATION_VERSION_FALLBACK = "2026-09-25"
#: Textract DetectDocumentText as wired in lib/textract (no API version string
#: is exposed by the SDK; this stamps the wiring).
TEXTRACT_VERSION = "detect-2026-09"
#: Fields below this extraction confidence make the document replay-eligible (spec §7).
REPLAY_LOW_CONFIDENCE = 0.5
#: Characters on a page that count as "full" text density for the quality score.
TEXT_DENSITY_FULL_CHARS = 1500

_FLAG_SENTENCES = {
    "low_res": "Low resolution",
    "blurry": "Blur",
    "low_contrast": "Low contrast",
    "skewed": "Skew",
    "glare": "Glare",
    "noisy": "Noise",
    "no_text": "No readable text",
    "photo": "Photo capture",
    "handwritten": "Handwriting",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def contract_parser_name(parser_name: str | None) -> str:
    """Spec §4: the router's ``jdf`` is ``jdf-cli`` at the contract boundary."""
    name = str(parser_name or "").strip().lower()
    if name in ("jdf", "jdf-ocr", "jdf-cli"):
        return "jdf-cli"
    return name or "unknown"


def current_jdf_cli_version() -> str:
    return os.environ.get("JDF_CLI_VERSION", "").strip() or "0.2.3"


def parser_version(parser_name: str, jdf: Any) -> str:
    if parser_name == "jdf-cli":
        meta = jdf.get("meta") if isinstance(jdf, dict) else None
        if isinstance(meta, dict):
            for key in ("version", "generator_version", "jdf_cli_version", "generator"):
                if isinstance(meta.get(key), str) and meta[key].strip():
                    return meta[key].strip()
        return current_jdf_cli_version()
    if parser_name == "textract":
        return TEXTRACT_VERSION
    if parser_name == "pymupdf":
        try:
            import fitz  # type: ignore

            return str(getattr(fitz, "VersionBind", None) or getattr(fitz, "version", ("unknown",))[0])
        except Exception:
            return "unknown"
    return "unknown"


def verification_version() -> str:
    try:
        from . import verification as v  # type: ignore
    except ImportError:
        try:
            import verification as v  # type: ignore
        except ImportError:
            return VERIFICATION_VERSION_FALLBACK
    for key in ("VERIFICATION_VERSION", "__version__", "VERSION"):
        val = getattr(v, key, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return VERIFICATION_VERSION_FALLBACK


def _quality_probe():
    try:
        from . import quality_probe  # type: ignore
        return quality_probe
    except ImportError:
        try:
            import quality_probe  # type: ignore
            return quality_probe
        except ImportError:
            return None


def _page_ocr_confidence(jdf: Any, page_index: int) -> float | None:
    """Mean of jdf-cli's ``ocr.blocks[].confidence`` on one page, or None."""
    pages = jdf.get("pages") if isinstance(jdf, dict) else None
    if not isinstance(pages, list) or page_index >= len(pages) or not isinstance(pages[page_index], dict):
        return None
    total, count = 0.0, 0
    for el in fx._walk_elements(pages[page_index].get("elements")):
        ocr = el.get("ocr")
        if isinstance(ocr, dict):
            for block in ocr.get("blocks") or []:
                conf = block.get("confidence") if isinstance(block, dict) else None
                if isinstance(conf, (int, float)):
                    total += float(conf)
                    count += 1
    return round(total / count, 4) if count else None


def _page_image_ratio(bundle: dict, page_no: int) -> float | None:
    images = [i for i in (bundle.get("images") or []) if isinstance(i, dict) and i.get("page") == page_no]
    chunks = [c for c in (bundle.get("chunks") or []) if isinstance(c, dict) and int(c.get("page") or 0) == page_no]
    if not chunks:
        return None
    return round(len(images) / max(1, len(chunks) + len(images)), 3)


def score_pages(bundle: dict, texts: list[str], intake: dict | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Per-page quality records and the signature assessment per page.

    Signals: the intake router's visual probe (``intake["visual_pages"]``),
    per-page OCR confidence (jdf-cli OCR blocks) or the document mean, text
    density (chars / ``TEXT_DENSITY_FULL_CHARS``), parse coverage (1.0 when
    the page yielded text, 0.0 when not — the only coverage fact a text
    bundle carries), image ratio (informative), signature quality on that
    page. Combination is ``quality_probe.page_quality_score``.
    """
    qp = _quality_probe()
    visual_pages = list((intake or {}).get("visual_pages") or [])
    visual_by_page = {int(v.get("page") or i + 1): v for i, v in enumerate(visual_pages) if isinstance(v, dict)}
    doc_ocr = bundle.get("ocr_confidence")
    jdf = bundle.get("jdf")
    pages: list[dict[str, Any]] = []
    signatures: list[dict[str, Any]] = []
    for i, text in enumerate(texts):
        page_no = i + 1
        visual = visual_by_page.get(page_no)
        ocr_conf = _page_ocr_confidence(jdf, i)
        if ocr_conf is None and isinstance(doc_ocr, (int, float)):
            ocr_conf = float(doc_ocr)
        chars = len((text or "").strip())
        text_density = round(min(1.0, chars / TEXT_DENSITY_FULL_CHARS), 3) if chars else 0.0
        parse_coverage = 1.0 if chars else 0.0
        image_ratio = _page_image_ratio(bundle, page_no)
        sig = fx.assess_signature(text or "", visual=visual, ocr_lines=None, page=page_no)
        signatures.append(sig)
        sig_quality = sig.get("quality") if sig.get("present") is not None else None
        flags = list((visual or {}).get("flags") or [])
        if not chars and "no_text" not in flags:
            flags.append("no_text")
        if qp is not None and hasattr(qp, "page_quality_score"):
            try:
                score, basis = qp.page_quality_score(
                    visual=visual, ocr_confidence=ocr_conf, text_density=text_density if chars else None,
                    parse_coverage=parse_coverage, image_ratio=image_ratio, signature_quality=sig_quality,
                )
            except Exception:
                log.exception("page_quality_score failed on page %s", page_no)
                score, basis = None, "page_quality_score raised; quality unknown"
        else:
            score, basis = None, "quality_probe unavailable — page quality unknown"
        pages.append(
            {
                "page": page_no,
                "quality_score": score,
                "flags": flags,
                "basis": basis,
                "ocr_confidence": ocr_conf,
                "text_density": text_density,
                "parse_coverage": parse_coverage,
                "image_ratio": image_ratio,
                "visual": visual,
            }
        )
    return pages, signatures


def quality_summary(pages: list[dict], signature: dict | None, numbers_flagged: int) -> str:
    """One calm sentence a reviewer can read at a glance."""
    total = len(pages)
    if not total:
        return "No pages were parsed."
    parts: list[str] = []
    counts: dict[str, int] = {}
    for p in pages:
        for flag in p.get("flags") or []:
            counts[flag] = counts.get(flag, 0) + 1
    for flag, n in counts.items():
        label = _FLAG_SENTENCES.get(flag, flag.replace("_", " ").capitalize())
        parts.append(f"{label} on {n} of {total} page{'s' if total != 1 else ''}")
    sig_quality = (signature or {}).get("quality")
    if signature and signature.get("present") is True and sig_quality not in (None, "clear", "unknown"):
        parts.append(f"signature is {sig_quality}")
    elif signature and signature.get("present") is False:
        parts.append("signature is missing")
    if numbers_flagged:
        parts.append(f"{numbers_flagged} number{'s' if numbers_flagged != 1 else ''} flagged for readability")
    scored = [p for p in pages if p.get("quality_score") is not None]
    if not parts:
        if not scored:
            return f"Page quality could not be measured on {total} page{'s' if total != 1 else ''}; no quality issues were detected from the text."
        return f"No quality issues detected on {total} page{'s' if total != 1 else ''}."
    sentence = "; ".join(parts)
    return sentence[0].upper() + sentence[1:] + "."


def review_summary(fields: list[dict]) -> dict[str, Any]:
    reasons: dict[str, int] = {}
    for f in fields:
        if f.get("review_required") and f.get("reason"):
            key = str(f["reason"]).split(":", 1)[0][:80]
            reasons[key] = reasons.get(key, 0) + 1
    top = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    return {
        "fields_total": len(fields),
        "fields_accepted": sum(1 for f in fields if f.get("field_state") == "accepted"),
        "fields_review": sum(1 for f in fields if f.get("review_required")),
        "fields_rejected": sum(1 for f in fields if f.get("field_state") == "rejected"),
        "fields_disputed": sum(1 for f in fields if f.get("field_state") == "disputed"),
        "reasons": [{"reason": r, "count": n} for r, n in top],
    }


def replay_state(fields: list[dict], parser_name: str, parser_ver: str, previous: dict | None = None) -> dict[str, Any]:
    """Spec §7: eligibility is recorded, execution is not (no durable replay path yet)."""
    reasons: list[str] = []
    low = [f["name"] for f in fields if f.get("value") is not None and float(f.get("extraction_confidence") or 0) < REPLAY_LOW_CONFIDENCE]
    if low:
        reasons.append(f"low extraction confidence (< {REPLAY_LOW_CONFIDENCE}) on {len(low)} field(s): {', '.join(low[:6])}")
    corrected = [f["name"] for f in fields if f.get("corrected")]
    if corrected:
        reasons.append(f"corrected field(s): {', '.join(corrected[:6])}")
    if parser_name == "jdf-cli" and parser_ver != current_jdf_cli_version():
        reasons.append(f"parser_version {parser_ver} older than current {current_jdf_cli_version()}")
    return {
        "eligible": bool(reasons),
        "reasons": reasons,
        "replayed": False,
        "history": list((previous or {}).get("history") or []),
    }


def decide_fields(fields: list[dict], *, verification: dict | None, document_type: str) -> tuple[list[dict], list[dict]]:
    """Plausibility rules (auto types) + real Z3 hits, then the 3-rule policy."""
    rules: list[dict] = []
    if document_type in ("auto_policy", "auto_claim"):
        rules = fx.coverage_plausibility(fields)
        fx.attach_plausibility(fields, rules)
    z3 = (verification or {}).get("z3") if isinstance(verification, dict) else None
    violations = z3.get("violations") if isinstance(z3, dict) else None
    fx.attach_z3_violations(fields, violations if isinstance(violations, list) else None)
    for f in fields:
        fx.apply_decision_policy(f)
    return fields, rules


def build_report(
    project_id: str,
    *,
    bundle: dict,
    verification: dict | None,
    filename: str,
    result: dict,
    job_id: str | None,
    intake: dict | None,
) -> dict[str, Any]:
    """The contract dict, not yet saved (``run_after_parse`` saves it)."""
    texts = fx.page_texts(bundle)
    layout = fx.page_layout(bundle)
    pname = contract_parser_name(bundle.get("parser_name") or (intake or {}).get("parser"))
    pver = parser_version(pname, bundle.get("jdf"))
    pages, signatures = score_pages(bundle, texts, intake)
    page_quality = [p["quality_score"] for p in pages]
    classification = fx.classify_document("\n".join(texts))
    classification["override"] = None
    doc_type = classification["document_type"]
    visual_pages = [p.get("visual") for p in pages]
    fields = fx.extract_fields(
        doc_type, texts, layout=layout, parser_name=pname, parse_confidence=bundle.get("parse_confidence"),
        ocr_confidence=bundle.get("ocr_confidence"), page_quality=page_quality, visual_pages=visual_pages,
    )
    fields, rules = decide_fields(fields, verification=verification, document_type=doc_type)
    scored = [s for s in page_quality if s is not None]
    doc_quality = round(sum(scored) / len(scored), 3) if scored else None
    quality_flags = sorted({flag for p in pages for flag in p.get("flags") or []})
    sig_field = next((f for f in fields if f.get("field_type") == "signature"), None)
    signature = (sig_field or {}).get("signature_quality") if sig_field else next((s for s in signatures if s.get("present") is not None), None)
    numbers_flagged = sum(1 for f in fields if (f.get("number_quality") or {}).get("review_required"))
    material = intake or {}
    if not material.get("material_type"):
        # No intake router output: the bundle's source_kind is the only fact.
        source_kind = str(bundle.get("source_kind") or "")
        material = {
            **material,
            "material_type": "text_file" if source_kind == "text" else "pdf",
            "modality": "scanned_pdf" if source_kind == "scanned" else ("text" if source_kind == "text" else "digital_pdf"),
        }
    z3 = (verification or {}).get("z3") if isinstance(verification, dict) else None
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "report_id": f"pr-{uuid.uuid4().hex[:16]}",
        "project_id": project_id,
        "document_id": result.get("document_id"),
        "revision_id": result.get("revision_id"),
        "revision_version": result.get("version"),
        "job_id": job_id,
        "filename": filename,
        "material_type": material.get("material_type"),
        "modality": material.get("modality"),
        "source_kind": bundle.get("source_kind") or material.get("source_kind"),
        "parser_name": pname,
        "parser_version": pver,
        "verification_version": verification_version(),
        "policy_version": POLICY_VERSION,
        "page_count": int(bundle.get("page_count") or len(texts) or 0),
        "pages": pages,
        "document_quality_score": doc_quality,
        "quality_flags": quality_flags,
        "classification": classification,
        "fields": fields,
        "plausibility": rules,
        "conflicts": [],
        "verification": {
            "z3_status": (verification or {}).get("z3_status") if isinstance(verification, dict) else None,
            "redhat_status": (verification or {}).get("redhat_status") if isinstance(verification, dict) else None,
            "z3_violation_count": len(z3.get("violations") or []) if isinstance(z3, dict) else None,
        },
        "review_summary": review_summary(fields),
        "quality_report": {
            "summary": quality_summary(pages, signature, numbers_flagged),
            "flags": quality_flags,
            "signature": signature,
            "numbers": {"flagged": numbers_flagged},
        },
        "laya": (intake or {}).get("laya"),
        "replay": replay_state(fields, pname, pver),
        "created_at": _now(),
        "_page_texts": texts,
        "_page_quality": page_quality,
        "_layout": layout,
    }
    return report


def refresh_report(report: dict[str, Any]) -> dict[str, Any]:
    """Recompute the derived blocks after a field changed (correct/dispute/override)."""
    fields = report.get("fields") or []
    report["review_summary"] = review_summary(fields)
    report["replay"] = replay_state(fields, str(report.get("parser_name") or ""), str(report.get("parser_version") or ""), report.get("replay"))
    return report


def reextract_for_type(report: dict[str, Any], document_type: str, *, verification: dict | None = None) -> dict[str, Any]:
    """Classification override: re-run extraction + policy over the stored page texts."""
    texts = list(report.get("_page_texts") or [])
    layout = report.get("_layout")
    fields = fx.extract_fields(
        document_type, texts, layout=layout if isinstance(layout, list) else None,
        parser_name=report.get("parser_name"), parse_confidence=None,
        ocr_confidence=None, page_quality=list(report.get("_page_quality") or []),
        visual_pages=[p.get("visual") for p in report.get("pages") or []],
    )
    fields, rules = decide_fields(fields, verification=verification, document_type=document_type)
    report["fields"] = fields
    report["plausibility"] = rules
    return refresh_report(report)


def attach_conflicts(project_id: str, report: dict[str, Any]) -> dict[str, Any]:
    """Cross-document conflicts over the project's saved reports plus this one."""
    try:
        from ..db import parsure_repository as repo
    except ImportError:
        from db import parsure_repository as repo  # type: ignore
    others = [r for r in repo.list_reports(project_id) if r.get("report_id") != report.get("report_id")]
    report["conflicts"] = fx.cross_document_conflicts(others + [report])
    return report


def run_after_parse(
    project_id: str,
    *,
    bundle: dict,
    verification: dict | None,
    filename: str,
    file_bytes: bytes | None,
    result: dict,
    job_id: str | None,
    intake: dict | None,
) -> dict[str, Any] | None:
    """Build, save and log the intake report. Returns ``{"report_id", "report"}`` or None on error.

    ``file_bytes`` is accepted for the contract but unused in V1: the visual
    probe already ran in the intake router and its result arrives in
    ``intake["visual_pages"]``; probing again here would be the duplicate
    router spec §8 forbids.
    """
    try:
        from ..db import parsure_repository as repo
    except ImportError:
        from db import parsure_repository as repo  # type: ignore
    try:
        report = build_report(
            project_id, bundle=bundle, verification=verification, filename=filename, result=result, job_id=job_id, intake=intake,
        )
        attach_conflicts(project_id, report)
        report_id = repo.save_report(project_id, report)
        rid = report_id
        repo.log_event(project_id, "intake_received", report_id=rid, payload={
            "filename": filename, "parser_name": report["parser_name"], "parser_version": report["parser_version"],
            "material_type": report["material_type"], "modality": report["modality"], "page_count": report["page_count"], "job_id": job_id,
        })
        repo.log_event(project_id, "quality_assessed", report_id=rid, payload={
            "document_quality_score": report["document_quality_score"], "flags": report["quality_flags"], "summary": report["quality_report"]["summary"],
        })
        repo.log_event(project_id, "classified", report_id=rid, payload={
            k: report["classification"].get(k) for k in ("document_type", "confidence", "basis")
        })
        repo.log_event(project_id, "fields_extracted", report_id=rid, payload={
            "fields_total": len(report["fields"]), "found": sum(1 for f in report["fields"] if f.get("value") is not None),
        })
        repo.log_event(project_id, "decision_applied", report_id=rid, payload={
            **{k: v for k, v in report["review_summary"].items() if k != "reasons"},
            "policy_version": POLICY_VERSION, "conflicts": len(report["conflicts"]),
        })
        return {"report_id": report_id, "report": repo.public_report(report)}
    except Exception:
        log.exception("parsure: run_after_parse failed for %s (project %s)", filename, project_id)
        return None
