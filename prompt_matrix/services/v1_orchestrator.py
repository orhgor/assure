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

Two Phase A additions live here because they are orchestration, not
extraction:

* **Grounded LLM fill** — the fields the label pass leaves empty are offered
  to ``services/llm_extraction`` (``PARSURE_LLM_EXTRACTION``, default on),
  which only returns a value it re-found verbatim in the page text. Its notes
  land in ``report["extraction_notes"]``.
* **Mixed bundles** (spec §6) — every page is classified on its own; when two
  or more pages carry *different confident* types the upload is split into
  ``documents`` segments, each classified and extracted separately, and the
  report is flagged ``mixed_bundle`` with ``material_type="mixed_bundle"``,
  ``modality="mixed"``. Boundary detection is **by page-level classification
  change only**: there is no visual boundary detection (blank pages, headers,
  page-number resets) in V1, so a two-page policy whose second page reads
  like a claim form will be split, and two same-type documents stapled
  together will not be. The flag routes the bundle to a reviewer either way.

Classification by evidence (2026-09-26, customer report on
``real_estate_policy_500697.pdf``: keywords typed a real-estate declarations
page as ``auto_claim`` and every auto-claim field was honestly "not found",
which the customer read as a broken OCR step). After the keyword pass and
before extraction, ``reclassify_by_evidence`` runs the cheap label pass for
every other type when the keyword type finds at most one field at confidence
under 0.7, and switches to the type whose fields are actually on the page
(≥ 2 found, strictly more than the keyword type). The switch is recorded in
``classification.basis``; the keyword answer stays in
``classification.detected``. When the type is still ``uncertain`` and the
grounded-LLM path is on, ``llm_extraction.classify_with_model`` may suggest
one type name; it is used only if that type's fields are then found, at
confidence ≤ 0.6, and its basis says so. No model ever names a value.

Family gate (2026-09-26, customer's CMS-1500 medical claim read as
``auto_policy``: keywords found only "accident", the evidence rule then
switched to the auto schema on policy number / insured name / signature —
fields every insurance form shares). ``field_extractor.document_family``
names the page's family from strong cues; a type may be chosen — by
keywords, by evidence or by the model — only when its family agrees or no
family dominates, and a switch needs ``RECLASSIFY_MIN_FOUND`` fields of which
at least one is type-specific (``fx.SHARED_FIELD_NAMES`` excluded). When the
cues name a family, the keyword answer is weak (uncertain or below
``RECLASSIFY_BELOW_CONFIDENCE``) and no schema of that family finds
``FAMILY_FALLBACK_MIN_TYPE_SPECIFIC`` type-specific fields, the report is
typed ``<family>_unknown`` with no taxonomy fields and
``classification.schema_mismatch = true`` — never a forced schema. Every
final type is validated against the family (``classification.validation``);
a reviewer's override to a type of another family whose fields are not on
the page marks the fields ``schema_mismatch`` (``fx.mark_schema_mismatch``)
and the document, not the fields, asks for a person.

Failure isolation: the pipeline treats the return value as advisory. Any
exception here is logged and turns into ``None`` so a review-backend bug can
never fail an ingest that already has its revision.
"""

from __future__ import annotations

import contextvars
import hashlib
import logging
import re
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

try:
    from ..services import field_discovery as _discovery
    from ..services import field_extractor as fx
    from ..services import llm_extraction as lx
    from ..services import raw_candidates as _raw
    from ..services import table_extraction as _tables
except ImportError:
    from services import field_discovery as _discovery  # type: ignore
    from services import field_extractor as fx  # type: ignore
    from services import llm_extraction as lx  # type: ignore
    from services import raw_candidates as _raw  # type: ignore
    from services import table_extraction as _tables  # type: ignore

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
#: Fewer characters than this across the whole upload and the document is
#: flagged ``no_text``: a one-page declarations PDF carries 1,500–3,000
#: characters of text layer; a scan the OCR could not read yields a few dozen
#: stray characters. The threshold is stated to the reader with the count.
NO_TEXT_MIN_CHARS = 200
#: ``reclassify_by_evidence`` runs when the keyword type finds at most this
#: many fields …
RECLASSIFY_MAX_FOUND = 1
#: … at a keyword confidence below this …
RECLASSIFY_BELOW_CONFIDENCE = 0.7
#: … and switches only to a type that finds at least this many fields …
RECLASSIFY_MIN_FOUND = 3
#: … of which at least this many are type-specific (not in fx.SHARED_FIELD_NAMES).
RECLASSIFY_MIN_TYPE_SPECIFIC = 1
#: A weak keyword answer is promoted to a family's schema only when that schema
#: finds at least this share of its fields (and FAMILY_FALLBACK_MIN_TYPE_SPECIFIC
#: type-specific ones). Customer run 2026-09-27: 2 of 12 fields (0.167) forced
#: ``auto_policy`` while the detected type said ``uncertain``; below this ratio
#: the answer stays ``<family>_unknown`` with the schema as a suggestion.
PROMOTION_MIN_RATIO = 0.25
#: A family's schema is kept (family fallback) only when it finds this many
#: type-specific fields; below it the document is ``<family>_unknown``.
FAMILY_FALLBACK_MIN_TYPE_SPECIFIC = 2
#: The family's confidence ceiling when the family fallback names a type: the
#: found ratio, and never above the keyword cap.
FAMILY_FALLBACK_CAP = fx.CLASSIFICATION_CAP
FAMILY_WORDS = {"auto": "Auto", "property": "Property", "real_estate_transaction": "Real estate transaction", "medical": "Medical",
                "field_report": "Field report"}
#: A model's type suggestion cannot earn more than this (spec §4: no fabricated
#: confidence; the number is the found ratio, and the cap says a model guess is
#: worth less than a keyword match, which is itself capped at 0.9).
MODEL_CLASSIFICATION_CAP = 0.6

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
    "form_template": "Unfilled form",
}

#: A page whose text is mostly numbered captions ("4. INSURED'S NAME …") with
#: nothing found under them is an unfilled form, not a document with missing
#: fields (demo set, 2026-09-27: a blank CMS-1500 read as a claim with 7 garbage
#: values). Flagged when this many numbered captions appear and ≤ 2 fields
#: carry values.
FORM_TEMPLATE_MIN_CAPTIONS = 8
_FORM_CAPTION_LINE = re.compile(r"^\s*\d{1,2}[a-z]?\.\s+[A-Z]", re.M)


def form_template_flag(texts: list[str], fields: list[dict]) -> dict[str, Any] | None:
    captions = sum(len(_FORM_CAPTION_LINE.findall(t or "")) for t in texts)
    found = fields_found_count(fields)
    if captions >= FORM_TEMPLATE_MIN_CAPTIONS and found <= 2:
        return {"flag": "form_template", "captions": captions, "fields_found": found,
                "sentence": f"This looks like an unfilled form: {captions} numbered captions and {found} filled value{'s' if found != 1 else ''}."}
    return None


def _now() -> str:
    # Microseconds kept (2026-09-27): ``parsure_repository.list_reports`` orders
    # by this value and second precision tied two saves of one document.
    return datetime.now(timezone.utc).isoformat()


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


def _page_orientation(bundle: dict, page_no: int) -> dict[str, Any] | None:
    """The OCR step's orientation record for one page (``jdf_converter.
    detect_orientation``: ``detected_degrees``, ``method``, ``basis``), or
    None when the parse made none (text-layer parse, Textract, detection off).
    Never synthesised: a page without a measurement has no record."""
    orientation = bundle.get("orientation") if isinstance(bundle, dict) else None
    pages = orientation.get("pages") if isinstance(orientation, dict) else None
    if not isinstance(pages, list):
        return None
    for entry in pages:
        if isinstance(entry, dict) and entry.get("page") == page_no:
            return {k: entry.get(k) for k in ("detected_degrees", "correction_degrees", "method", "basis") if k in entry}
    return None


def score_pages(bundle: dict, texts: list[str], intake: dict | None, layout: list[list[dict]] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Per-page quality records and the signature assessment per page.

    Signals: the intake router's visual probe (``intake["visual_pages"]``),
    per-page OCR confidence (jdf-cli OCR blocks) or the document mean, text
    density (chars / ``TEXT_DENSITY_FULL_CHARS``), parse coverage (1.0 when
    the page yielded text, 0.0 when not — the only coverage fact a text
    bundle carries), image ratio (informative), signature quality on that
    page (``layout`` — ``page_layout`` — lets the probe measure the band by
    the label element, 2026-09-28), and the OCR step's orientation record
    when the page was a scan. Combination is ``quality_probe.page_quality_score``.
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
        page_segments = layout[i] if isinstance(layout, list) and i < len(layout) else None
        sig = fx.assess_signature(text or "", visual=visual, ocr_lines=None, page=page_no, layout=page_segments)
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
                "orientation": _page_orientation(bundle, page_no),
                "visual": visual,
            }
        )
    return pages, signatures


def quality_summary(pages: list[dict], signature: dict | None, numbers_flagged: int, *, documents: list[dict] | None = None) -> str:
    """One calm sentence a reviewer can read at a glance."""
    total = len(pages)
    if not total:
        return "No pages were read."
    parts: list[str] = []
    if documents and len(documents) > 1:
        parts.append(f"{len(documents)} documents detected in one upload (" + ", ".join(
            f"{d['document_type']} p.{d['pages'][0]}" + (f"–{d['pages'][-1]}" if len(d["pages"]) > 1 else "") for d in documents) + ")")
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


#: The reason a reviewer sees first when a typed document yielded no field at
#: all: in every case examined so far (2026-09-26) the type was wrong, not the
#: OCR — and the fix is one click on the record page.
WRONG_TYPE_REASON = "document type may be wrong — change it and the fields are re-read"
UNCERTAIN_TYPE_REASON = "document type is uncertain — choose it and the fields are read"


def family_of_unknown(document_type: Any) -> str | None:
    """``medical_unknown`` → ``medical``; None for any other type name."""
    name = str(document_type or "")
    if name.endswith("_unknown"):
        fam = name[: -len("_unknown")]
        if fam in fx.DOCUMENT_FAMILIES:
            return fam
    return None


def type_words(document_type: Any) -> str:
    """``auto_claim`` → "Auto claim" — the same rendering the pages use
    (``routers/parsure_routes.words``) without importing a Flask module here."""
    if document_type in (None, "", "uncertain", "unknown", "other"):
        return "an uncertain type"
    fam = family_of_unknown(document_type)
    if fam:
        return f"{FAMILY_WORDS.get(fam, fam)} — type unknown"
    return str(document_type).replace("_", " ").replace("-", " ").strip().capitalize()


SCHEMA_MISMATCH_REASON = "wrong document type — the fields of this type are not on the page"


def mismatch_sentence(classification: dict[str, Any] | None) -> str | None:
    """The one line a mismatched document shows, or None when the schema fits.
    "Wrong document type — read as Medical claim?" when the family's evidence
    proposes a type; otherwise the family and that no schema of it fits."""
    if not isinstance(classification, dict) or not classification.get("schema_mismatch"):
        return None
    suggestion = classification.get("suggestion") if isinstance(classification.get("suggestion"), dict) else None
    if suggestion and suggestion.get("document_type"):
        return f"Wrong document type — read as {type_words(suggestion['document_type'])}?"
    validation = classification.get("validation") if isinstance(classification.get("validation"), dict) else {}
    fam = validation.get("family") or family_of_unknown(classification.get("document_type"))
    if fam and fam != "unknown":
        return f"Wrong document type — the page reads as a {FAMILY_WORDS.get(fam, fam).lower()} document, but no {FAMILY_WORDS.get(fam, fam).lower()} schema fits it."
    return "Wrong document type — choose the document type."


def fields_found_count(fields: list[dict]) -> int:
    return sum(1 for f in fields if f.get("value") is not None)


def extraction_sentence(document_type: Any, fields_total: int, fields_found: int, text_chars: int | None) -> str | None:
    """The document-level sentence for "nothing was read", or None when
    something was.

    Said plainly and first, because twelve "not found" rows read as twelve
    failures (customer report, 2026-09-26) when the fact is one: no field of
    the chosen type is on the page. Under ``NO_TEXT_MIN_CHARS`` the more
    likely fact is an unreadable scan, and the character count is given so
    the reader can judge — never an OCR confidence this code did not measure.
    """
    if fields_found > 0:
        return None
    chars = int(text_chars) if text_chars is not None else None
    little_text = chars is not None and chars < NO_TEXT_MIN_CHARS
    if family_of_unknown(document_type):
        return None  # mismatch_sentence says it
    if fields_total > 0:
        head = f"No fields could be read as {type_words(document_type)}"
        if little_text:
            return f"{head} — the pages carry {chars} characters of text; the file may be a scan the OCR could not read."
        return f"{head}."
    if little_text:
        return f"The pages could not be read ({chars} characters of text); the file may be a scan the OCR could not read."
    if document_type in (None, "", "uncertain"):
        return "No fields could be read: the document type is uncertain."
    return None


def compose_quality_summary(report: dict[str, Any]) -> str:
    """``quality_report.summary`` = the extraction sentence (when nothing was
    read) + the page-quality sentence. Re-run after a type change so the
    sentence names the type that was actually tried."""
    qr = report.setdefault("quality_report", {})
    fields = report.get("fields") or []
    classification = report.get("classification") if isinstance(report.get("classification"), dict) else {}
    document_type = classification.get("document_type")
    sentence = qr.get("quality_sentence")
    if sentence is None:
        sentence = str(qr.get("summary") or "")
        qr["quality_sentence"] = sentence
    lead = mismatch_sentence(classification) or extraction_sentence(document_type, len(fields), fields_found_count(fields), qr.get("text_chars"))
    qr["extraction_sentence"] = lead
    qr["summary"] = f"{lead} {sentence}".strip() if lead else sentence
    return qr["summary"]


def review_summary(fields: list[dict], *, document_type: Any = None, schema_mismatch: bool = False) -> dict[str, Any]:
    """Counts a reviewer can trust: ``fields_review`` uses the same rule as
    the review queue (``field_extractor.field_needs_review``), so the summary
    line, the queue header, the data count and the analytics column agree.
    ``fields_found`` is always present; when it is 0 for a typed document the
    first reason says the type is the likely cause. A ``schema_mismatch``
    document has one reason and no field asks for review; ``evidence_states``
    counts the five outcomes so the reader sees how many fields were absent
    on readable pages versus unreadable ones."""
    reasons: dict[str, int] = {}
    for f in fields:
        if fx.field_needs_review(f) and f.get("reason"):
            key = str(f["reason"]).split(":", 1)[0][:80]
            reasons[key] = reasons.get(key, 0) + 1
    top = [{"reason": r, "count": n} for r, n in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]
    found = fields_found_count(fields)
    if schema_mismatch or family_of_unknown(document_type):
        top.insert(0, {"reason": SCHEMA_MISMATCH_REASON, "count": 1})
    elif fields and found == 0:
        top.insert(0, {"reason": WRONG_TYPE_REASON, "count": len(fields)})
    elif not fields and document_type in (None, "", "uncertain"):
        top.insert(0, {"reason": UNCERTAIN_TYPE_REASON, "count": 0})
    states: dict[str, int] = {}
    for f in fields:
        st = f.get("evidence_state")
        if st:
            states[st] = states.get(st, 0) + 1
    return {
        "fields_total": len(fields),
        "fields_found": found,
        "fields_accepted": sum(1 for f in fields if f.get("field_state") == "accepted"),
        "fields_review": sum(1 for f in fields if fx.field_needs_review(f)),
        "fields_rejected": sum(1 for f in fields if f.get("field_state") == "rejected"),
        "fields_disputed": sum(1 for f in fields if f.get("field_state") == "disputed"),
        # 2026-09-27: absences and debris counted apart from review items — the
        # customer's summary "12 fields need review" was 9 absences + 3 fields.
        "fields_not_found": sum(1 for f in fields if f.get("field_state") == "not_found"),
        "fields_suspect": sum(1 for f in fields if f.get("evidence_state") == "found_suspect"),
        "evidence_states": states,
        "schema_mismatch": bool(schema_mismatch),
        "reasons": top,
    }


#: Rerun bounds (handoff 2026-09-27, "Rerun Thrash"): a report is re-read at
#: most this many times (classification overrides that re-extract, and
#: replays), and never again after two consecutive reruns that did not raise
#: ``fields_found``. Versioned with POLICY_VERSION; the history is append-only.
RERUN_MAX_ATTEMPTS = 3
RERUN_NO_IMPROVEMENT_RUNS = 2
RERUN_TRIGGERS = ("classification_override", "replay")
#: The four facts of a field a replay compares; ``value`` and ``element_id``
#: say the same text was read from the same element, ``field_state`` says the
#: policy decided the same.
REPLAY_FIELD_KEYS = ("name", "value", "element_id", "field_state")


def rerun_stop_rule(replay: dict | None) -> str | None:
    """The rule that stops another rerun of this report, or ``None`` when one
    may run. Read from the append-only ``replay.history``: the attempt count
    is ``len(history)`` (``attempts`` mirrors it), so a caller cannot reset the
    bound by editing a counter."""
    replay = replay if isinstance(replay, dict) else {}
    history = [h for h in (replay.get("history") or []) if isinstance(h, dict)]
    # Pipeline passes (``trigger`` "pipeline:…", 2026-09-27) are the run's own
    # second looks and do not spend the reviewer's rerun budget.
    manual = [h for h in history if h.get("trigger") in RERUN_TRIGGERS]
    attempts = max(int(replay.get("attempts") or 0), len(manual))
    if attempts >= RERUN_MAX_ATTEMPTS:
        return f"max_attempts: {attempts} of {RERUN_MAX_ATTEMPTS} reruns used"
    recent = [h for h in history if h.get("trigger") in RERUN_TRIGGERS][-RERUN_NO_IMPROVEMENT_RUNS:]
    if len(recent) == RERUN_NO_IMPROVEMENT_RUNS and all(h.get("improved") is False for h in recent):
        return f"no_improvement: the last {RERUN_NO_IMPROVEMENT_RUNS} reruns did not raise fields_found"
    return None


def record_rerun(report: dict[str, Any], *, trigger: str, before_found: int, after_found: int,
                 snapshot_before: str | None, snapshot_after: str | None, fields_changed: list[str] | None = None,
                 mapping_changed: dict[str, Any] | None = None, grounded: bool | None = None, grounded_reason: str | None = None) -> dict[str, Any]:
    """Append one entry to ``replay.history`` and count the attempt.

    ``snapshot_after`` is the hash of the report as the rerun left it, before
    this entry was appended — an entry cannot contain the hash of a report
    that contains the entry. The row's own ``snapshot`` (stamped on save) is
    the hash *with* the entry. Returns the entry.

    Additive keys (plan V4 Parts 1.4 / 3, 2026-09-28; the entry's existing
    keys keep their meaning): ``fields_changed`` — the names whose
    ``(value, element_id, field_state)`` differ after the rerun;
    ``mapping_changed`` — the raw-candidate projection's counts for the
    remap (``raw_candidates.project_candidates``); ``grounded`` /
    ``grounded_reason`` — whether this rerun reached a model (a manual replay
    on the web tier declares ``False`` and why). Each is written only when the
    caller supplies it."""
    if trigger not in RERUN_TRIGGERS:
        raise ValueError(f"unknown rerun trigger: {trigger}")
    replay = report.setdefault("replay", {})
    history = list(replay.get("history") or [])
    redhat = report.get("redhat") if isinstance(report.get("redhat"), dict) else {}
    entry = {
        "at": _now(),
        "trigger": trigger,
        "policy_version": report.get("policy_version") or POLICY_VERSION,
        "node_id_policy": report.get("node_id_policy"),
        "redhat_policy": redhat.get("policy"),
        "fields_found_before": int(before_found),
        "fields_found_after": int(after_found),
        "improved": int(after_found) > int(before_found),
        "snapshot_before": snapshot_before,
        "snapshot_after": snapshot_after,
    }
    if fields_changed is not None:
        entry["fields_changed"] = sorted(str(n) for n in fields_changed)
    if mapping_changed is not None:
        entry["mapping_changed"] = mapping_changed
    if grounded is not None:
        entry["grounded"] = bool(grounded)
        entry["grounded_reason"] = grounded_reason
    history.append(entry)
    replay["history"] = history
    replay["attempts"] = sum(1 for h in history if h.get("trigger") in RERUN_TRIGGERS)
    replay["passes"] = sum(1 for h in history if str(h.get("trigger") or "").startswith("pipeline:"))
    replay["max_attempts"] = RERUN_MAX_ATTEMPTS
    replay["stop_rule"] = rerun_stop_rule(replay)
    return entry


PIPELINE_TRIGGERS = ("pipeline:llm_grounded", "pipeline:redhat_targeted")


def record_pipeline_pass(report: dict[str, Any], *, trigger: str, fields_changed: list[str], before_found: int, after_found: int,
                         basis: str | None = None) -> dict[str, Any]:
    """Append the run's own second look to ``replay.history`` (plan Part 2.4,
    2026-09-27): the grounded model pass after the label pass, and the pass
    the Red-Hat critique asked for. Same append-only ledger as a reviewer's
    rerun, a different trigger, and it does not count toward ``attempts``
    (``replay.passes`` counts these) — the reviewer's three reruns stay theirs.
    ``improved`` is "a field changed", not only "more fields found": a debris
    value replaced by a grounded one is the improvement the pass is for."""
    if trigger not in PIPELINE_TRIGGERS:
        raise ValueError(f"unknown pipeline trigger: {trigger}")
    replay = report.setdefault("replay", {})
    history = list(replay.get("history") or [])
    redhat = report.get("redhat") if isinstance(report.get("redhat"), dict) else {}
    entry = {
        "at": _now(),
        "trigger": trigger,
        "policy_version": report.get("policy_version") or POLICY_VERSION,
        "node_id_policy": report.get("node_id_policy"),
        "redhat_policy": redhat.get("policy"),
        "fields_found_before": int(before_found),
        "fields_found_after": int(after_found),
        "fields_changed": sorted(fields_changed),
        "improved": bool(fields_changed),
        "basis": basis,
        "snapshot_before": None,
        "snapshot_after": None,
    }
    history.append(entry)
    replay["history"] = history
    replay["attempts"] = sum(1 for h in history if h.get("trigger") in RERUN_TRIGGERS)
    replay["passes"] = sum(1 for h in history if str(h.get("trigger") or "").startswith("pipeline:"))
    replay["max_attempts"] = RERUN_MAX_ATTEMPTS
    replay["stop_rule"] = rerun_stop_rule(replay)
    return entry


def field_facts(fields: list[dict]) -> dict[str, tuple]:
    """``{name: (value, element_id, field_state)}`` — what a replay compares."""
    out: dict[str, tuple] = {}
    for f in fields or []:
        if not isinstance(f, dict):
            continue
        span = f.get("source_span") if isinstance(f.get("source_span"), dict) else {}
        element_id = f.get("element_id") if f.get("element_id") is not None else span.get("element_id")
        out[str(f.get("name"))] = (f.get("value"), element_id, f.get("field_state"))
    return out


def replay_proof(before: dict[str, tuple], after: dict[str, tuple], *, snapshot_before: str | None, snapshot_after: str | None) -> dict[str, Any]:
    """Compare two ``field_facts`` maps. ``deterministic`` is true only when
    every field name is present on both sides with identical facts."""
    names = sorted(set(before) | set(after))
    changed = [n for n in names if before.get(n) != after.get(n)]
    return {
        "deterministic": not changed,
        "fields_identical": len(names) - len(changed),
        "fields_total": len(names),
        "changed": changed,
        "compared": list(REPLAY_FIELD_KEYS),
        "snapshot_before": snapshot_before,
        "snapshot_after": snapshot_after,
    }


def replay_state(fields: list[dict], parser_name: str, parser_ver: str, previous: dict | None = None, *, document_type: Any = None) -> dict[str, Any]:
    """Spec §7 eligibility, plus the rerun ledger (2026-09-27): ``attempts`` /
    ``max_attempts`` / ``stop_rule`` / ``last_proof`` / ``history`` are carried
    from ``previous`` unchanged — the history is append-only and only
    :func:`record_rerun` writes to it."""
    reasons: list[str] = []
    low = [f["name"] for f in fields if f.get("value") is not None and float(f.get("extraction_confidence") or 0) < REPLAY_LOW_CONFIDENCE]
    if low:
        reasons.append(f"low extraction confidence (< {REPLAY_LOW_CONFIDENCE}) on {len(low)} field(s): {', '.join(low[:6])}")
    corrected = [f["name"] for f in fields if f.get("corrected")]
    if corrected:
        reasons.append(f"corrected field(s): {', '.join(corrected[:6])}")
    if fields and fields_found_count(fields) == 0:
        reasons.append(f"no field was found as {document_type or 'this type'} — re-read after the document type is changed")
    if parser_name == "jdf-cli" and parser_ver != current_jdf_cli_version():
        reasons.append(f"parser_version {parser_ver} older than current {current_jdf_cli_version()}")
    prev = previous if isinstance(previous, dict) else {}
    history = list(prev.get("history") or [])
    state = {
        "eligible": bool(reasons),
        "reasons": reasons,
        "replayed": bool(prev.get("replayed")),
        "history": history,
        "attempts": max(int(prev.get("attempts") or 0), sum(1 for h in history if isinstance(h, dict) and h.get("trigger") in RERUN_TRIGGERS)),
        "passes": sum(1 for h in history if isinstance(h, dict) and str(h.get("trigger") or "").startswith("pipeline:")),
        "max_attempts": RERUN_MAX_ATTEMPTS,
        "last_proof": prev.get("last_proof"),
    }
    state["stop_rule"] = rerun_stop_rule(state)
    return state


def _page_range(pages: list[int]) -> str:
    return f"p.{pages[0]}" + (f"–{pages[-1]}" if len(pages) > 1 else "")


def segment_pages(texts: list[str]) -> list[dict[str, Any]]:
    """Spec §6 "Mixed Bundles": split an upload into documents by page-level
    classification change — and by nothing else (no visual boundary
    detection in V1; see the module docstring).

    Each page is classified alone. A confident page (not ``uncertain``) that
    differs from the running segment's type opens a new segment; an uncertain
    page joins the running segment (a continuation page rarely repeats the
    title vocabulary), or the first confident segment when it comes before
    one. When fewer than two distinct confident types appear the whole upload
    is one segment classified from its joined text — the pre-Phase A
    behaviour. Otherwise each segment is re-classified from its own joined
    text so its ``confidence``/``basis`` describe the segment, and the
    boundary basis names the pages where the type changed.
    """
    texts = list(texts or [])
    all_pages = list(range(1, len(texts) + 1))
    whole = fx.classify_document("\n".join(texts))
    single = [{"index": 0, "pages": all_pages, "document_type": whole["document_type"], "confidence": whole["confidence"],
               "basis": whole["basis"], "matched_keywords": whole.get("matched_keywords") or [], "page_types": None,
               "family": whole.get("family")}]
    if len(texts) < 2:
        return single
    page_types = [fx.classify_document(t or "")["document_type"] for t in texts]
    segments: list[dict[str, Any]] = []
    leading: list[int] = []
    for page_no, ptype in zip(all_pages, page_types):
        if ptype == "uncertain":
            (segments[-1]["pages"] if segments else leading).append(page_no)
            continue
        if segments and segments[-1]["document_type"] == ptype:
            segments[-1]["pages"].append(page_no)
        else:
            segments.append({"document_type": ptype, "pages": leading + [page_no]})
            leading = []
    if len({seg["document_type"] for seg in segments}) < 2:
        single[0]["page_types"] = page_types
        return single
    out: list[dict[str, Any]] = []
    for idx, seg in enumerate(segments):
        cls = fx.classify_document("\n".join(texts[p - 1] or "" for p in seg["pages"]))
        doc_type = cls["document_type"] if cls["document_type"] != "uncertain" else seg["document_type"]
        out.append({
            "index": idx, "pages": seg["pages"], "document_type": doc_type, "confidence": cls["confidence"],
            "basis": f"pages {_page_range(seg['pages'])} classified alone as {seg['document_type']}; segment text: {cls['basis']}",
            "matched_keywords": cls.get("matched_keywords") or [], "page_types": [page_types[p - 1] for p in seg["pages"]],
            "family": cls.get("family"),
        })
    return out


def bundle_classification(segments: list[dict]) -> dict[str, Any]:
    """The report-level ``classification`` for a mixed bundle: the type is the
    literal ``mixed_bundle`` (no single ICP type describes the upload) and the
    basis names the page boundaries and how they were found."""
    boundary = " → ".join(f"{s['document_type']} ({_page_range(s['pages'])})" for s in segments)
    return {
        "document_type": "mixed_bundle",
        "confidence": round(min(float(s.get("confidence") or 0.0) for s in segments), 3),
        "basis": f"page-level classification change: {boundary}; boundaries by per-page keyword classification only (no visual boundary detection in V1)",
        "matched_keywords": [],
        "segments": [{k: s[k] for k in ("index", "pages", "document_type", "confidence")} for s in segments],
    }


def segment_texts(texts: list[str], pages: list[int]) -> list[str]:
    """The page list cut to one segment: pages outside it before the segment
    are blanked (page numbers, layout and quality indexes stay aligned with
    the document) and pages after it are dropped (so the signature fallback's
    "last page" is the segment's last page)."""
    keep = set(pages)
    last = max(pages) if pages else 0
    return [(texts[i] if (i + 1) in keep else "") for i in range(min(last, len(texts)))]


# --------------------------------------------------------------------------
# Per-stage timings (plan P1 "latency tracked per family and modality")
# --------------------------------------------------------------------------

#: Stage names ``report["timings_ms"]`` always carries, in pipeline order.
#: ``extract`` is the label pass + decision policy *without* the model call,
#: which is ``llm_extract`` (the one stage whose wall time depends on a
#: network); ``total`` is the whole of ``build_report``. A stage that did not
#: run reads 0.0, never a guess.
TIMING_STAGES = ("layout", "classify", "extract", "llm_extract", "quality", "total")
#: The four latency classes the benchmark plan groups documents by; derived
#: from what the report already knows (``latency_class``), never declared.
LATENCY_CLASSES = ("policy_form", "claim_packet", "photo_signature", "mixed_bundle")
_CLAIM_TYPES = frozenset({"medical_claim", "auto_claim", "property_claim"})
_PHOTO_MODALITIES = frozenset({"phone_photo", "screenshot"})
_PHOTO_MATERIALS = frozenset({"photo", "image", "screenshot"})

_TIMINGS: contextvars.ContextVar[dict[str, float] | None] = contextvars.ContextVar("parsure_timings", default=None)


@contextmanager
def _timed(stage: str):
    """Add the block's wall time (ms, ``perf_counter``) to ``stage`` of the
    timings dict ``build_report`` opened for this call; a no-op outside one, so
    the helpers stay callable on their own (tests, ``reextract_for_type``)."""
    t0 = perf_counter()
    try:
        yield
    finally:
        acc = _TIMINGS.get()
        if acc is not None:
            acc[stage] = round(acc.get(stage, 0.0) + (perf_counter() - t0) * 1000.0, 3)


def latency_class(material_type: Any, modality: Any, document_type: Any, n_documents: int) -> str:
    """One of ``LATENCY_CLASSES`` for the analytics bucket a run belongs to.

    Checked in order: several documents in one upload → ``mixed_bundle``; a
    photo or scanned image (material ``photo``/``image``/``screenshot`` or
    modality ``phone_photo``/``screenshot``; a scanned *PDF* is not — it has
    the same page shape as a digital one and its cost is OCR, tracked by
    ``parser_name``) → ``photo_signature``; a claim schema → ``claim_packet``;
    everything else → ``policy_form``."""
    if (n_documents or 0) > 1 or str(material_type or "") == "mixed_bundle":
        return "mixed_bundle"
    if str(material_type or "") in _PHOTO_MATERIALS or str(modality or "") in _PHOTO_MODALITIES:
        return "photo_signature"
    if str(document_type or "") in _CLAIM_TYPES:
        return "claim_packet"
    return "policy_form"


def llm_fill_missing(
    document_type: str,
    texts: list[str],
    fields: list[dict],
    *,
    completion: Any = None,
    notes: list[str],
    parser_name: str | None,
    parse_confidence: float | None,
    ocr_confidence: float | None,
    page_quality: list[float | None] | None,
    visual_pages: list[dict] | None,
    layout: list[list[dict]] | None,
    project_id: str | None = None,
    execution: dict | None = None,
    hints: dict[str, str] | None = None,
    only: list[str] | None = None,
) -> list[dict]:
    """Offer the label pass's empty fields to the grounded LLM pass and merge
    what it can prove. The signature field is never offered; a field the pass
    cannot ground stays exactly as it was. Never raises (the pass itself
    does not); ``notes`` collects its skip/reject lines.

    ``execution`` (2026-09-27, plan Part 2.1/7.1 — "did the model run?")
    receives ``llm_grounding = {status, model, fields_offered, fields_grounded,
    fields_filled, candidates_rejected, ms, reason}``: ``ran`` when the model
    answered, ``skipped`` with the pass's own reason (off, no text, unparsable
    answer, exception), ``not_needed`` when nothing was missing. ``only``
    restricts the offer to named fields and ``hints`` adds per-field notes to
    the prompt (the Red-Hat-targeted second pass)."""
    specs = {s.name: s for s in fx.FIELD_TAXONOMY.get(document_type) or []}
    missing = [specs[f["name"]] for f in fields
               if f.get("value") is None and f["name"] in specs and specs[f["name"]].field_type != "signature"
               and (only is None or f["name"] in only)]
    # ``model_path`` says which model answered: ``injected`` (a test's or a
    # caller's completion) or ``app:<model>`` — the application's own model
    # path (``llm_extraction.default_completion`` on the configured backend).
    # A reviewer read ``completion=None`` in the production callers as "no
    # model" (2026-09-27); None means the app path, and the ledger now says so.
    stats: dict[str, Any] = {"status": "not_needed", "model": None, "model_path": "app" if lx.is_app_completion(completion) else "injected",
                             "fields_offered": len(missing), "fields_grounded": 0, "fields_filled": [], "candidates_rejected": 0, "ms": 0.0, "reason": None}
    if execution is not None:
        execution["llm_grounding"] = stats
    if not missing:
        stats["reason"] = "every offered field already had a value"
        return fields
    before_notes = len(notes)
    started = perf_counter()
    try:
        with _timed("llm_extract"):
            filled = lx.extract_missing_fields(
                document_type, texts, missing, completion=completion, notes=notes, parser_name=parser_name,
                parse_confidence=parse_confidence, ocr_confidence=ocr_confidence, page_quality=page_quality,
                visual_pages=visual_pages, layout=layout, project_id=project_id, hints=hints,
            )
    except Exception as exc:  # noqa: BLE001 — advisory pass
        log.exception("llm_fill_missing failed")
        notes.append(f"llm extraction skipped: {type(exc).__name__}: {exc}")
        stats.update(status="failed", reason=f"{type(exc).__name__}: {exc}"[:200], ms=round((perf_counter() - started) * 1000.0, 3))
        return fields
    stats["ms"] = round((perf_counter() - started) * 1000.0, 3)
    new_notes = notes[before_notes:]
    skipped = next((n for n in new_notes if n.startswith("llm extraction skipped")), None)
    stats["candidates_rejected"] = sum(1 for n in new_notes if n.startswith("llm candidate rejected"))
    by_name = {f["name"]: f for f in filled if f.get("value") is not None}
    stats["fields_grounded"] = len(by_name)
    stats["fields_filled"] = sorted(by_name)
    stats["model"] = next((f.get("grounding_model") for f in by_name.values() if f.get("grounding_model")), None) or (
        (lx.current_model_id() if lx.llm_extraction_enabled() else None) if lx.is_app_completion(completion) else "injected")
    stats["model_path"] = lx.model_path_for(completion, stats["model"])
    if skipped:
        stats["status"] = "disabled" if "PARSURE_LLM_EXTRACTION is off" in skipped else "skipped"
        stats["reason"] = skipped.split(":", 1)[1].strip() if ":" in skipped else skipped
    else:
        stats["status"] = "ran"
    return [by_name.get(f["name"], f) for f in fields]


def reclassify_by_evidence(document_type: Any, confidence: Any, texts: list[str], *, keyword_hits: list | None = None) -> dict[str, Any] | None:
    """Pick the type whose fields are on the page when the keyword type's are not.

    Runs only when ``document_type`` finds at most ``RECLASSIFY_MAX_FOUND``
    fields with the label pass and its keyword confidence is below
    ``RECLASSIFY_BELOW_CONFIDENCE`` (``None`` counts as below). Then every
    other type in ``FIELD_TAXONOMY`` gets the same cheap label pass and the
    one with the most found fields wins — if its family passes the gate
    (``fx.type_allowed``), it finds at least ``RECLASSIFY_MIN_FOUND`` fields of
    which ``RECLASSIFY_MIN_TYPE_SPECIFIC`` are type-specific, and strictly more
    than the original; ties go to taxonomy order, so the outcome is
    deterministic. Returns
    the replacement classification (``document_type``, ``confidence`` = found
    ratio capped at ``CLASSIFICATION_CAP``, ``basis`` naming both counts,
    ``detected`` = the keyword answer, ``method``) or None to keep the keyword
    answer. Regex only: no model, nothing inferred.
    """
    doc_type = str(document_type or "uncertain")
    current_total = len(fx.FIELD_TAXONOMY.get(doc_type) or [])
    current_found = fx.count_found_fields(doc_type, texts) if current_total else 0
    if current_found > RECLASSIFY_MAX_FOUND:
        return None
    conf = float(confidence) if isinstance(confidence, (int, float)) else None
    if conf is not None and conf >= RECLASSIFY_BELOW_CONFIDENCE:
        return None
    family = fx.document_family("\n".join(t or "" for t in texts))
    # The family gate: only schemas of the detected family compete (all of
    # them when no family dominates), and a candidate must show at least one
    # type-specific field — policy number, insured name and signature are on
    # every insurance form and won the customer's CMS-1500 for auto_policy.
    candidates = [t for t in fx.FIELD_TAXONOMY if t != doc_type and fx.type_allowed(t, family["family"])]
    if not candidates:
        return None
    names = {t: fx.found_field_names(t, texts) for t in candidates}
    specific = {t: [n for n in names[t] if n not in fx.SHARED_FIELD_NAMES] for t in candidates}
    eligible = [t for t in candidates if len(names[t]) >= RECLASSIFY_MIN_FOUND and len(specific[t]) >= RECLASSIFY_MIN_TYPE_SPECIFIC]
    if not eligible:
        return None
    best = max(eligible, key=lambda t: (len(names[t]), len(specific[t]), -list(fx.FIELD_TAXONOMY).index(t)))
    best_found = len(names[best])
    if best_found <= current_found:
        return None
    best_total = len(fx.FIELD_TAXONOMY[best])
    hits = len(keyword_hits or [])
    if current_total:
        versus = f" vs {doc_type} {current_found}/{current_total} (keywords said {doc_type}, {hits} hit{'s' if hits != 1 else ''})"
    else:
        versus = f" (keywords said uncertain, {hits} hit{'s' if hits != 1 else ''})"
    gate = f"; family gate: {family['family']}" if family["family"] != "unknown" else ""
    return {
        "document_type": best,
        "confidence": round(min(fx.CLASSIFICATION_CAP, best_found / best_total), 3),
        "basis": f"reclassified by extraction evidence: {best} {best_found}/{best_total} fields found{versus}{gate}",
        "matched_keywords": list(keyword_hits or []),
        "method": "extraction_evidence",
        "detected": {"document_type": doc_type, "confidence": confidence, "basis": None, "matched_keywords": list(keyword_hits or [])},
        "evidence": {"found": best_found, "total": best_total, "type_specific": len(specific[best]), "type_specific_fields": specific[best],
                     "current_found": current_found, "current_total": current_total},
        "family": family,
    }


def classify_with_model_if_uncertain(texts: list[str], *, completion: Any, project_id: str | None, notes: list[str],
                                     detected: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Ask the configured model for ONE type name and accept it only when it
    passes the family gate and that type's fields are then found (≥ 1, at
    least one type-specific) by the label pass. Confidence is the
    found ratio capped at ``MODEL_CLASSIFICATION_CAP``; the basis names the
    model and the count. Honors ``PARSURE_LLM_EXTRACTION``; never raises —
    every skip is a line in ``notes``."""
    if not lx.llm_extraction_enabled():
        return None
    try:
        out = lx.classify_with_model(texts, completion=completion, project_id=project_id)
    except Exception as exc:  # noqa: BLE001 — advisory
        log.exception("classify_with_model failed")
        notes.append(f"model type suggestion skipped: {type(exc).__name__}: {exc}")
        return None
    suggested = out.get("document_type") if isinstance(out, dict) else None
    if not suggested or suggested not in fx.FIELD_TAXONOMY:
        notes.append(f"model type suggestion skipped: {(out or {}).get('basis') or 'no usable answer'}")
        return None
    family = fx.document_family("\n".join(t or "" for t in texts))
    if not fx.type_allowed(suggested, family["family"]):
        notes.append(f"model suggested {suggested} but the page's cues say {family['family']} ({family['basis']}); type stays uncertain")
        return None
    names = fx.found_field_names(suggested, texts)
    specific = [n for n in names if n not in fx.SHARED_FIELD_NAMES]
    found, total = len(names), len(fx.FIELD_TAXONOMY[suggested])
    if found == 0:
        notes.append(f"model suggested {suggested} but none of its {total} fields were found; type stays uncertain")
        return None
    if not specific:
        notes.append(f"model suggested {suggested} but only shared fields were found ({', '.join(names)}); type stays uncertain")
        return None
    if found < RECLASSIFY_MIN_FOUND or len(specific) < FAMILY_FALLBACK_MIN_TYPE_SPECIFIC or found / max(1, total) < PROMOTION_MIN_RATIO:
        # Same bar as every other promotion (2026-09-28, demo set: a 10-page
        # claims packet became property_claim on 1 of 9 fields because the
        # model said so). The suggestion is kept for the grounded pass.
        notes.append(f"model suggested {suggested} but only {found}/{total} field(s) were found ({len(specific)} type-specific); "
                     f"below the evidence bar ({RECLASSIFY_MIN_FOUND} found, {FAMILY_FALLBACK_MIN_TYPE_SPECIFIC} type-specific, {PROMOTION_MIN_RATIO:.0%}); type stays uncertain")
        return {"_suggestion_only": {"document_type": suggested, "found": found, "total": total, "type_specific": len(specific), "type_specific_fields": specific, "source": "model"}}
    model = out.get("model") or "model"
    notes.append(f"model type suggestion accepted: {suggested} ({found}/{total} fields found)")
    return {
        "document_type": suggested,
        "confidence": round(min(MODEL_CLASSIFICATION_CAP, found / total), 3),
        "basis": f"model suggestion ({model}), confirmed by {found} field{'s' if found != 1 else ''} found ({found}/{total})",
        "matched_keywords": [],
        "method": "model_suggestion",
        "detected": detected or {},
        "evidence": {"found": found, "total": total, "type_specific": len(specific), "type_specific_fields": specific},
        "family": family,
    }


def family_suggestion(family: str, texts: list[str]) -> dict[str, Any] | None:
    """The family's schema with the most found fields (type-specific first),
    as ``{"document_type", "found", "total", "type_specific", "type_specific_fields"}``
    — what the mismatch line proposes. None for an unknown family."""
    types = [t for t in fx.FIELD_TAXONOMY if fx.TYPE_FAMILY.get(t) == family]
    if not types:
        return None
    scored = []
    for t in types:
        names = fx.found_field_names(t, texts)
        specific = [n for n in names if n not in fx.SHARED_FIELD_NAMES]
        scored.append((len(specific), len(names), -types.index(t), t, names, specific))
    scored.sort(reverse=True)
    _, _, _, best, names, specific = scored[0]
    return {"document_type": best, "found": len(names), "total": len(fx.FIELD_TAXONOMY[best]), "type_specific": len(specific), "type_specific_fields": specific}


def family_fallback(document_type: Any, confidence: Any, texts: list[str], *, keyword_hits: list | None = None) -> dict[str, Any] | None:
    """The last word when the page's cues name a family and the keyword answer
    is weak (``uncertain`` or below ``RECLASSIFY_BELOW_CONFIDENCE``).

    The family's schema with the most type-specific fields is chosen when it
    finds at least ``FAMILY_FALLBACK_MIN_TYPE_SPECIFIC`` of them (method
    ``family_evidence``); otherwise the document is ``<family>_unknown`` —
    no taxonomy fields, ``schema_mismatch`` true, and ``suggestion`` naming the
    family's nearest schema so the record page can offer it. A type that
    already belongs to the family and finds enough type-specific fields is
    kept (None). A confident keyword answer (≥ 0.7) is never second-guessed
    here: the "nothing extracted as Auto claim — check the type" path stays
    for a well-named type whose labels the OCR did not read.
    """
    doc_type = str(document_type or "uncertain")
    conf = float(confidence) if isinstance(confidence, (int, float)) else None
    if doc_type in fx.FIELD_TAXONOMY and conf is not None and conf >= RECLASSIFY_BELOW_CONFIDENCE:
        return None
    family = fx.document_family("\n".join(t or "" for t in texts))
    fam = family["family"]
    if fam == "unknown":
        return None
    if fx.TYPE_FAMILY.get(doc_type) == fam and len(fx.type_specific_found(doc_type, texts)) >= FAMILY_FALLBACK_MIN_TYPE_SPECIFIC:
        return None
    suggestion = family_suggestion(fam, texts)
    hits = len(keyword_hits or [])
    said = f"keywords said {doc_type}, {hits} hit{'s' if hits != 1 else ''}"
    detected = {"document_type": doc_type, "confidence": confidence, "basis": None, "matched_keywords": list(keyword_hits or [])}
    ratio = (suggestion["found"] / suggestion["total"]) if suggestion and suggestion["total"] else 0.0
    if suggestion and suggestion["type_specific"] >= FAMILY_FALLBACK_MIN_TYPE_SPECIFIC and ratio >= PROMOTION_MIN_RATIO:
        best = suggestion["document_type"]
        return {
            "document_type": best,
            "confidence": round(min(FAMILY_FALLBACK_CAP, ratio), 3),
            "basis": (f"family {fam} ({family['basis']}); {best} finds {suggestion['found']}/{suggestion['total']} fields, "
                      f"{suggestion['type_specific']} type-specific ({', '.join(suggestion['type_specific_fields'][:6])}) ({said})"),
            "matched_keywords": list(keyword_hits or []),
            "method": "family_evidence",
            "detected": detected,
            "evidence": {k: suggestion[k] for k in ("found", "total", "type_specific", "type_specific_fields")},
            "family": family,
            "schema_mismatch": False,
            "suggestion": None,
        }
    tried = f"{suggestion['document_type']} finds {suggestion['type_specific']} type-specific field{'s' if suggestion['type_specific'] != 1 else ''} ({suggestion['found']}/{suggestion['total']} in all)" if suggestion else "no schema in the taxonomy"
    return {
        "document_type": f"{fam}_unknown",
        "confidence": None,
        "basis": (f"family {fam} ({family['basis']}) but no {fam} schema fits: {tried}; below {FAMILY_FALLBACK_MIN_TYPE_SPECIFIC} type-specific fields "
                  f"or under {PROMOTION_MIN_RATIO:.0%} of the schema's fields no schema is forced ({said})"),
        "matched_keywords": list(keyword_hits or []),
        "method": "family_fallback",
        "detected": detected,
        "evidence": {k: suggestion[k] for k in ("found", "total", "type_specific", "type_specific_fields")} if suggestion else None,
        "family": family,
        "schema_mismatch": True,
        "suggestion": suggestion,
    }


def validate_classification(document_type: Any, texts: list[str], *, family: dict[str, Any] | None = None) -> dict[str, Any]:
    """``classification.validation``: the page's family against the chosen
    type's, with the cues that named it. ``agrees`` is true when the families
    match, no family dominates, or the type is outside the taxonomy
    (``uncertain``, ``mixed_bundle``, ``<family>_unknown``). ``type_specific``
    counts the chosen type's type-specific fields on the page — the evidence
    that outranks a cue when a reviewer's override disagrees with it."""
    fam = family if isinstance(family, dict) and family.get("family") else fx.document_family("\n".join(t or "" for t in texts))
    doc_type = str(document_type or "uncertain")
    type_family = fx.TYPE_FAMILY.get(doc_type) or family_of_unknown(doc_type) or ("mixed" if doc_type == "mixed_bundle" else None)
    agrees = fx.type_allowed(doc_type, fam["family"])
    specific = fx.type_specific_found(doc_type, texts) if doc_type in fx.FIELD_TAXONOMY else []
    return {
        "family": fam["family"],
        "type_family": type_family,
        "agrees": bool(agrees),
        "cues": list(fam.get("cues") or []),
        "basis": fam.get("basis"),
        "type_specific": len(specific),
        "type_specific_fields": specific,
    }


def settle_segment_type(seg: dict[str, Any], texts: list[str], *, completion: Any, project_id: str | None, notes: list[str], llm: bool = True) -> None:
    """Apply the evidence rule, then (still uncertain, LLM path on) the model
    suggestion, then the family fallback, to one segment in place; finally
    validate the type against the page's family. Adds ``detected`` /
    ``method`` only when the type changed, so an untouched segment keeps its
    shape; ``validation`` / ``schema_mismatch`` / ``suggestion`` are always
    set."""
    detected = {k: seg.get(k) for k in ("document_type", "confidence", "basis", "matched_keywords")}
    evidence = reclassify_by_evidence(seg.get("document_type"), seg.get("confidence"), texts, keyword_hits=seg.get("matched_keywords"))
    if evidence is None and seg.get("document_type") == "uncertain" and llm and any((t or "").strip() for t in texts):
        evidence = classify_with_model_if_uncertain(texts, completion=completion, project_id=project_id, notes=notes, detected=detected)
        if isinstance(evidence, dict) and "_suggestion_only" in evidence:
            seg["suggestion"] = evidence["_suggestion_only"]
            evidence = None
    if evidence is None and any((t or "").strip() for t in texts):
        evidence = family_fallback(seg.get("document_type"), seg.get("confidence"), texts, keyword_hits=seg.get("matched_keywords"))
        if evidence is not None:
            notes.append(f"family fallback: {evidence['document_type']} — {evidence['basis']}")
    if evidence is not None:
        evidence["detected"] = {**detected, **{k: v for k, v in evidence.get("detected", {}).items() if v is not None}}
        seg.update(evidence)
    seg["validation"] = validate_classification(seg.get("document_type"), texts, family=seg.get("family"))
    seg.setdefault("schema_mismatch", False)
    seg.setdefault("suggestion", None)
    if not seg["validation"]["agrees"] and seg["validation"]["type_specific"] < FAMILY_FALLBACK_MIN_TYPE_SPECIFIC:
        seg["schema_mismatch"] = True
        seg["suggestion"] = seg.get("suggestion") or family_suggestion(seg["validation"]["family"], texts)
    seg["uncertainty"] = uncertainty_of(seg, texts)


UNCERTAINTY_CODES = {
    "no_text": "no readable text on the segment",
    "too_few_keywords": "fewer than the minimum keyword matches for any type",
    "keyword_tie": "two types matched the same keywords and found the same number of fields",
    "family_without_schema": "the page names a family but no schema of that family finds enough fields",
    "weak_promotion": "a schema found too few of its fields to be forced",
    "family_disagrees": "the chosen type's family disagrees with the page's cues",
}


def uncertainty_of(seg: dict[str, Any], texts: list[str]) -> dict[str, Any] | None:
    """Why a segment is ``uncertain`` / ``<family>_unknown``, with what was
    tried — reason codes, the keywords that did match, the shared fields
    searched and the ones found. ``None`` for a typed segment. Customer,
    2026-09-27: "document_type = uncertain, fields = {}" was a dead end."""
    doc_type = str(seg.get("document_type") or "uncertain")
    if doc_type != "uncertain" and not family_of_unknown(doc_type):
        return None
    basis = str(seg.get("basis") or "")
    codes: list[str] = []
    text = "\n".join(t or "" for t in texts)
    if not text.strip():
        codes.append("no_text")
    if "below minimum" in basis:
        codes.append("too_few_keywords")
    if basis.startswith("tie between"):
        codes.append("keyword_tie")
    if family_of_unknown(doc_type):
        codes.append("family_without_schema")
        if "under" in basis and "%" in basis:
            codes.append("weak_promotion")
    validation = seg.get("validation") or {}
    if validation and validation.get("agrees") is False:
        codes.append("family_disagrees")
    found = fx.found_field_counts(texts) if text.strip() else {}
    searched = sorted(fx.SHARED_FIELD_NAMES)
    return {
        "reason_codes": codes or ["too_few_keywords"],
        "reasons": [UNCERTAINTY_CODES[c] for c in (codes or ["too_few_keywords"])],
        "keywords_matched": list(seg.get("matched_keywords") or []),
        "family": (seg.get("family") or {}).get("family") if isinstance(seg.get("family"), dict) else None,
        "fields_searched": searched,
        "fields_found": [name for name, n in sorted(found.items()) if n],
        "suggestion": (seg.get("suggestion") or {}).get("document_type") if isinstance(seg.get("suggestion"), dict) else None,
        "basis": basis or None,
    }


def page_coverage(documents: list[dict], fields: list[dict], texts: list[str], page_quality: list[float | None]) -> list[dict[str, Any]]:
    """Per page: which segment/type read it, how many values came from it and
    how readable it was — so a 54-page bundle says which pages were searched
    and which yielded nothing (customer, 2026-09-27: "sparse extraction, no
    way to see which pages were looked at")."""
    readability = fx.page_readability(texts, page_quality)
    seg_of_page: dict[int, dict] = {}
    for seg in documents:
        for p in seg.get("pages") or []:
            seg_of_page[int(p)] = seg
    per_page: dict[int, int] = {}
    for f in fields:
        page = (f.get("source_span") or {}).get("page") if f.get("value") is not None else None
        if page:
            per_page[int(page)] = per_page.get(int(page), 0) + 1
    out = []
    for i in range(len(texts)):
        page = i + 1
        seg = seg_of_page.get(page) or {}
        out.append({
            "page": page,
            "segment": seg.get("index"),
            "document_type": seg.get("document_type"),
            "fields_found": per_page.get(page, 0),
            "readability": readability[i] if i < len(readability) else None,
            "quality_score": page_quality[i] if i < len(page_quality) else None,
            "searched": bool(seg),
        })
    return out


def promote_with_grounded_pass(seg: dict[str, Any], texts: list[str], *, layout, parser_name, parse_confidence, ocr_confidence,
                               page_quality, visual_pages, completion: Any, project_id: str | None, notes: list[str],
                               execution: dict | None = None) -> bool:
    """A segment no schema fits by its labels (``<family>_unknown``, or
    ``uncertain`` with a model suggestion below the bar) gets one grounded
    model pass over the suggested schema's fields *before* extraction. Filled
    forms print values in boxes the label pass cannot read as "label: value"
    (demo set 2026-09-28: a CMS-1500 with a patient name, member id and tax id
    read as an unfilled ``medical_unknown`` because its captions were rightly
    rejected as values), while the grounded pass finds them verbatim. The type
    is promoted only on that evidence — ``RECLASSIFY_MIN_FOUND`` grounded
    fields, ``FAMILY_FALLBACK_MIN_TYPE_SPECIFIC`` type-specific — and the
    report says so (``method: llm_grounded_evidence``). Returns True when the
    segment was promoted."""
    doc_type = str(seg.get("document_type") or "")
    suggestion = seg.get("suggestion") if isinstance(seg.get("suggestion"), dict) else None
    if not suggestion or not (family_of_unknown(doc_type) or doc_type == "uncertain"):
        return False
    target = suggestion.get("document_type")
    if target not in fx.FIELD_TAXONOMY or not lx.llm_extraction_enabled():
        return False
    fields = fx.extract_fields(target, texts, layout=layout, parser_name=parser_name, parse_confidence=parse_confidence,
                               ocr_confidence=ocr_confidence, page_quality=page_quality, visual_pages=visual_pages)
    stats: dict[str, Any] = {}
    fields = llm_fill_missing(target, texts, fields, completion=completion, notes=notes, parser_name=parser_name,
                              parse_confidence=parse_confidence, ocr_confidence=ocr_confidence, page_quality=page_quality,
                              visual_pages=visual_pages, layout=layout, project_id=project_id, execution=stats)
    found = [f["name"] for f in fields if f.get("value") is not None and f.get("field_type") != "signature"]
    specific = [n for n in found if n not in fx.SHARED_FIELD_NAMES]
    total = len(fx.FIELD_TAXONOMY[target])
    if execution is not None:
        execution["grounded_promotion"] = {"tried": target, "found": len(found), "type_specific": len(specific), "total": total,
                                           "promoted": False, "llm": stats.get("llm_grounding")}
    if len(found) < RECLASSIFY_MIN_FOUND or len(specific) < FAMILY_FALLBACK_MIN_TYPE_SPECIFIC:
        notes.append(f"grounded pass over {target}: {len(found)}/{total} field(s) found ({len(specific)} type-specific); below the evidence bar, type stays {doc_type}")
        return False
    detected = {k: seg.get(k) for k in ("document_type", "confidence", "basis", "matched_keywords")}
    seg.update({
        "document_type": target,
        "confidence": round(min(fx.CLASSIFICATION_CAP, len(found) / total), 3),
        "basis": f"grounded model pass over the suggested schema: {target} {len(found)}/{total} fields found verbatim, {len(specific)} type-specific ({', '.join(specific[:6])}); labels alone said {doc_type}",
        "method": "llm_grounded_evidence",
        "detected": detected,
        "evidence": {"found": len(found), "total": total, "type_specific": len(specific), "type_specific_fields": specific},
        "schema_mismatch": False,
        "suggestion": None,
    })
    seg["validation"] = validate_classification(target, texts, family=seg.get("family"))
    seg["uncertainty"] = None
    if execution is not None:
        execution["grounded_promotion"]["promoted"] = True
    notes.append(f"type promoted by grounded evidence: {target} ({len(found)}/{total} fields found, {len(specific)} type-specific)")
    return True


def extract_segment_fields(
    document_type: str,
    texts: list[str],
    *,
    layout: list[list[dict]] | None,
    parser_name: str | None,
    parse_confidence: float | None,
    ocr_confidence: float | None,
    page_quality: list[float | None],
    visual_pages: list[dict | None],
    verification: dict | None,
    notes: list[str],
    completion: Any = None,
    project_id: str | None = None,
    llm: bool = True,
    schema_mismatch: bool = False,
    execution: dict | None = None,
    candidates: list[dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Label pass → table pass → raw-candidate projection → grounded LLM fill
    (``llm=True``) → plausibility/Z3 → 3-rule policy, for one document
    (segment). ``llm=False`` leaves a note instead. ``schema_mismatch=True``
    (the type's family disagrees with the page and its fields are not there)
    skips the model — no grounded value can make a wrong schema right — and
    marks every field ``schema_mismatch`` after the policy ran.

    ``candidates`` (plan V4 Part 1.3, 2026-09-28) is the segment's slice of the
    raw pool; ``raw_candidates.project_candidates`` maps them onto the schema
    before the model is asked, so a fact the page states under a label the
    regex separator did not read ("REPORT ID · RPT-…") becomes a field
    without a model call, and the model is offered fewer fields."""
    fields = fx.extract_fields(
        document_type, texts, layout=layout, parser_name=parser_name, parse_confidence=parse_confidence,
        ocr_confidence=ocr_confidence, page_quality=page_quality, visual_pages=visual_pages,
    )
    # Table pass (plan Part 3, 2026-09-27) — between the label pass and the
    # model: fills the money/number/date fields the labels left empty from
    # the bundle's table elements (``services/table_extraction``), so the
    # model is asked about fewer fields. Then, for a segment no schema fits,
    # discovery lists the page's label/value pairs (``services/field_discovery``)
    # as ``discovered_fields`` — not taxonomy fields, so counts stay honest.
    fields = _tables.run_table_pass(
        document_type, fields, texts=texts, execution=execution, notes=notes, parser_name=parser_name,
        parse_confidence=parse_confidence, ocr_confidence=ocr_confidence, page_quality=page_quality, visual_pages=visual_pages,
    )
    _discovery.run_discovery(document_type, texts, layout, completion=completion, project_id=project_id, notes=notes, llm=llm, execution=execution,
                             parser_name=parser_name, parse_confidence=parse_confidence, ocr_confidence=ocr_confidence,
                             page_quality=page_quality, visual_pages=visual_pages)
    projection: dict[str, Any] | None = None
    if candidates and not schema_mismatch and document_type in fx.FIELD_TAXONOMY:
        try:
            fields, projection = _raw.project_candidates(
                document_type, candidates, fields, texts=texts, layout=layout, parser_name=parser_name, parse_confidence=parse_confidence,
                ocr_confidence=ocr_confidence, page_quality=page_quality, visual_pages=visual_pages,
            )
            mapped = [n for n, e in projection["fields"].items() if e.get("outcome") == "mapped" and e.get("replaced")]
            if mapped:
                notes.append(f"raw candidates projected onto {document_type}: {len(mapped)} field(s) filled from the pool ({', '.join(mapped[:6])})")
            if projection["conflicts"]:
                notes.append(f"raw candidates disagree with the label pass on {len(projection['conflicts'])} field(s): "
                             f"{', '.join(c['field'] for c in projection['conflicts'][:6])} — both kept, see conflicts")
        except Exception as exc:  # noqa: BLE001 — the projection is additive; a failure must not empty the fields
            log.exception("raw candidate projection failed for %s", document_type)
            projection = {"status": "failed", "document_type": document_type, "reason": f"{type(exc).__name__}: {exc}"[:200],
                          "candidates": len(candidates), "counts": {}, "fields": {}, "candidates_log": {}, "conflicts": []}
    elif execution is not None and "projection" not in execution:
        reason = ("schema mismatch — nothing to project onto" if schema_mismatch else
                  "no schema for this type" if document_type not in fx.FIELD_TAXONOMY else "empty candidate pool for this segment")
        projection = {"status": "not_run", "document_type": document_type, "reason": reason, "candidates": len(candidates or []),
                      "counts": {}, "fields": {}, "candidates_log": {}, "conflicts": []}
    if schema_mismatch:
        if fields:
            notes.append(f"llm extraction skipped: schema mismatch — {document_type} fields are not applicable to this page")
    elif llm:
        fields = llm_fill_missing(
            document_type, texts, fields, completion=completion, notes=notes, parser_name=parser_name,
            parse_confidence=parse_confidence, ocr_confidence=ocr_confidence, page_quality=page_quality,
            visual_pages=visual_pages, layout=layout, project_id=project_id, execution=execution,
        )
    elif any(f.get("value") is None and f.get("field_type") != "signature" for f in fields):
        notes.append("llm extraction skipped: not run on this path (label pass only)")
        if execution is not None:
            execution["llm_grounding"] = {"status": "skipped", "model": None, "fields_offered": 0, "fields_grounded": 0, "fields_filled": [],
                                          "candidates_rejected": 0, "ms": 0.0, "reason": "not run on this path (label pass only)"}
    fields, rules = decide_fields(fields, verification=verification, document_type=document_type)
    if schema_mismatch:
        fx.mark_schema_mismatch(fields)
    if projection is not None:
        _raw.finalize_projection(projection, fields)
        if execution is not None:
            execution["projection"] = _raw.merge_projection_logs(execution.get("projection") if execution.get("projection", {}).get("status") == "completed" else None, projection)
    return fields, rules


def decide_fields(fields: list[dict], *, verification: dict | None, document_type: str) -> tuple[list[dict], list[dict]]:
    """Plausibility rules (auto types) + real Z3 hits, then the 3-rule policy."""
    rules: list[dict] = []
    if document_type in ("auto_policy", "auto_claim"):
        rules = fx.coverage_plausibility(fields)
        fx.attach_plausibility(fields, rules)
    z3 = (verification or {}).get("z3") if isinstance(verification, dict) else None
    violations = z3.get("violations") if isinstance(z3, dict) else None
    fx.attach_z3_violations(fields, violations if isinstance(violations, list) else None)
    fx.attach_verification_confidence(fields, verification)
    for f in fields:
        fx.apply_decision_policy(f)
    return fields, rules


def attach_tree_node_ids(fields: list[dict], tree: dict | None) -> int:
    """``tree_node_id`` + ``source_span.node_id`` on every field whose chunk id
    (``field_source_node_id``, the jdf-cli ``p1e0``) a paragraph of the saved
    Assure tree carries as ``meta.chunk_id`` (jdf_converter stamps it since
    2026-09-26). That paragraph id is what the shell renders as
    ``data-node-id``, so Review can jump to the exact node. Returns how many
    fields were addressed; a ``None`` tree addresses none and says nothing."""
    if not isinstance(tree, dict) or not fields:
        return 0
    by_chunk: dict[str, str] = {}
    node_ids: set[str] = set()
    nodes: dict[str, dict] = {}
    root: str | None = None
    stack = list(tree.get("body") or [])
    for node in stack:
        if isinstance(node, dict) and node.get("id"):
            root = str(node["id"])  # the first body section: the document's first node
            break
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if node.get("id"):
            node_ids.add(str(node["id"]))
            nodes[str(node["id"])] = node
        meta = node.get("meta") if isinstance(node.get("meta"), dict) else {}
        cid = str(meta.get("chunk_id") or "")
        if cid and node.get("id") and cid not in by_chunk:
            by_chunk[cid] = str(node["id"])
        stack.extend(node.get("children") or [])
    n = 0
    for f in fields:
        fsn = str(f.get("field_source_node_id") or "")
        # On the import path the bundle's ``jdf`` IS the saved tree, so the
        # layout already carries paragraph ids (measured 2026-09-26:
        # ``p-ffa247f9d47f`` on every found field); a chunk id maps through
        # meta.chunk_id instead.
        nid = fsn if fsn in node_ids else by_chunk.get(fsn)
        evidence = f.get("evidence") if isinstance(f.get("evidence"), dict) else None
        if nid is None and evidence and (evidence.get("kind") == "absent" or evidence.get("anchor_kind")) and root:
            # An absent field with no layout anchor (flat text, no chunk ids) —
            # or a probe-found signature / suspect anchored to a layout node the
            # tree does not carry — hangs off the document's first node so the
            # graph has no orphan; the evidence says it is the root, not a
            # paragraph it was read from.
            nid = root
            f["field_source_node_id"] = f["field_source_node_id"] or root
            evidence["anchor_node_id"] = evidence.get("anchor_node_id") or root
            evidence["anchor_kind"] = evidence.get("anchor_kind") or "document_root"
        f["tree_node_id"] = nid
        if nid:
            n += 1
            if isinstance(f.get("source_span"), dict):
                f["source_span"]["node_id"] = nid
                _attach_element_span(f, nodes.get(nid))
    return n


def _attach_element_span(field: dict, node: dict | None) -> None:
    """``source_span.element_id`` and, when the addressed paragraph carries
    ``meta.elements`` (jdf_converter, ``eid-v1``), ``source_span.node_offsets``
    = the value's ``{start_char, end_char}`` inside that paragraph's content,
    so the shell can highlight the exact span rather than the whole node.

    The offsets are located by the element the field already names (its
    ``element_id``, from the layout) and the field's ``raw`` text inside that
    element's range; a field whose layout segment *was* the paragraph already
    carries offsets from ``build_found_field`` and they are kept. Nothing is
    invented: a value whose text cannot be found in the node gets no offsets.
    """
    span = field.get("source_span")
    if not isinstance(span, dict) or field.get("value") is None:
        return
    span.setdefault("element_id", field.get("element_id"))
    if not isinstance(node, dict):
        return
    meta = node.get("meta") if isinstance(node.get("meta"), dict) else {}
    elements = [e for e in (meta.get("elements") or []) if isinstance(e, dict)]
    if not elements:
        return
    content = str(node.get("content") or "")
    raw = str(field.get("raw") or "")
    if isinstance(span.get("node_offsets"), dict):
        offsets = span["node_offsets"]
        if span.get("element_id") is None:
            for e in elements:
                if isinstance(e.get("start_char"), int) and isinstance(e.get("end_char"), int) and e["start_char"] <= int(offsets.get("start_char", -1)) < e["end_char"]:
                    span["element_id"] = field["element_id"] = e.get("element_id")
                    break
        return
    if not raw or not content:
        return
    target = next((e for e in elements if e.get("element_id") and e["element_id"] == span.get("element_id")), None)
    idx = -1
    if target is not None and isinstance(target.get("start_char"), int):
        idx = content.find(raw, target["start_char"])
        if idx != -1 and isinstance(target.get("end_char"), int) and idx >= target["end_char"]:
            idx = -1
    if idx == -1:
        idx = content.find(raw)
        if idx != -1 and target is None:
            for e in elements:
                if isinstance(e.get("start_char"), int) and isinstance(e.get("end_char"), int) and e["start_char"] <= idx < e["end_char"]:
                    span["element_id"] = field["element_id"] = e.get("element_id")
                    if isinstance(e.get("bbox"), list) and len(e["bbox"]) == 4 and span.get("span_type") == "text_range":
                        span["span_type"], span["bbox"] = "bbox_relative", e["bbox"]
                    break
    if idx != -1:
        span["node_offsets"] = {"start_char": idx, "end_char": idx + len(raw)}


def graph_integrity(fields: list[dict]) -> dict[str, Any]:
    """``{"fields", "anchored", "orphans", "absent_anchored", "element_ids",
    "policy", "checked_at", "basis"}`` — every field's link into the JDF graph
    (``element_ids``: found fields that name an ``eid-v1`` element;
    ``policy``: ``field_extractor.NODE_ID_POLICY``), counted once the
    tree ids are attached. A field is anchored when ``field_source_node_id``
    (or ``tree_node_id``) is set; an absent field's anchor is the node it was
    searched from (``evidence.anchor_node_id``). ``orphans`` is the count with
    no node at all — zero whenever the parse produced any node id; a flat
    text upload with none says so in ``basis`` rather than inventing one."""
    total = len(fields)
    anchored = sum(1 for f in fields if f.get("field_source_node_id") or f.get("tree_node_id"))
    absent_anchored = sum(1 for f in fields if f.get("value") is None and (f.get("field_source_node_id") or f.get("tree_node_id")))
    element_ids = sum(1 for f in fields if f.get("value") is not None and f.get("element_id"))
    orphans = total - anchored
    if not total:
        basis = "no fields"
    elif orphans == 0:
        basis = "every field names a JDF node (found: the node its value sits on; absent: the anchor it was searched from)"
    else:
        basis = f"{orphans} field{'s' if orphans != 1 else ''} without a node: the document yielded no node ids to anchor to"
    # Plan Part 2.7 / 9.2 (2026-09-27): one number an export gate can read
    # (``integrity_score`` = anchored / fields, 1.0 when there is no field),
    # the negative-evidence count (absent fields anchored to what was searched)
    # and the orphans by name — never a bare count a reviewer cannot act on.
    return {"fields": total, "anchored": anchored, "orphans": orphans, "absent_anchored": absent_anchored,
            "element_ids": element_ids, "integrity_score": round(anchored / total, 4) if total else 1.0,
            "negative_evidence": sum(1 for f in fields if f.get("value") is None and isinstance(f.get("evidence"), dict) and f["evidence"].get("kind") == "absent"),
            "orphan_list": [str(f.get("name")) for f in fields if not (f.get("field_source_node_id") or f.get("tree_node_id"))],
            "policy": fx.NODE_ID_POLICY, "checked_at": _now(), "basis": basis}


def build_report(
    project_id: str,
    *,
    bundle: dict,
    verification: dict | None,
    filename: str,
    result: dict,
    job_id: str | None,
    intake: dict | None,
    completion: Any = None,
    tree: dict | None = None,
) -> dict[str, Any]:
    """The contract dict, not yet saved (``run_after_parse`` saves it).

    ``completion`` is the injectable model call for the grounded LLM pass
    (tests); production leaves it None and ``llm_extraction`` uses the app's
    model path.
    """
    timings: dict[str, float] = {stage: 0.0 for stage in TIMING_STAGES}
    t_start = perf_counter()
    token = _TIMINGS.set(timings)
    try:
        # Tables are collected from the bundle here (plan Part 3, 2026-09-27)
        # and handed to the extraction segments through an ``extraction_scope``
        # — ``extract_segment_fields`` sees texts and layout, not the bundle,
        # and jdf-cli's table elements are not layout segments.
        tables = _tables.collect_tables(bundle)
        with _tables.extraction_scope(tables) as scope:
            report = _build_report_timed(
                project_id, bundle=bundle, verification=verification, filename=filename, result=result, job_id=job_id,
                intake=intake, completion=completion, tree=tree, timings=timings, t_start=t_start,
            )
        _tables.annotate_report(report, scope)
        # The stored source JDF (services/source_jdf, 2026-09-28): the pipeline
        # that stored it puts the descriptor on ``intake``; the report carries
        # it so the shell can render the page behind the fields. None when
        # nothing was stored — never a guessed URL.
        report["source_jdf"] = intake.get("source_jdf") if isinstance(intake, dict) else None
        report["intake_extra"] = intake_extra(intake)
        return report
    finally:
        _TIMINGS.reset(token)


#: Intake keys the report build reads (2026-09-27, grep over v1_orchestrator,
#: vision, pdf_ingest): the router's parser choice, material/modality/source
#: kind, the visual probe pages and the LAYA triage block. Anything else the
#: caller put on ``intake`` is recorded verbatim in ``report["intake_extra"]``
#: (plan Part 4.3) — an audit trail for parameters the pipeline does not use
#: yet (``claim_context``, ``adjuster_notes`` …) so they are neither lost nor
#: silently ignored.
CONSUMED_INTAKE_KEYS = frozenset({"parser", "parser_name", "material_type", "modality", "source_kind", "visual_pages", "laya", "source_jdf"})


def intake_extra(intake: dict | None) -> dict[str, Any] | None:
    """The ``intake`` entries no report key consumed, or None when there are none."""
    if not isinstance(intake, dict):
        return None
    extra = {str(k): v for k, v in intake.items() if str(k) not in CONSUMED_INTAKE_KEYS}
    return extra or None


def _build_report_timed(
    project_id: str,
    *,
    bundle: dict,
    verification: dict | None,
    filename: str,
    result: dict,
    job_id: str | None,
    intake: dict | None,
    completion: Any,
    tree: dict | None,
    timings: dict[str, float],
    t_start: float,
) -> dict[str, Any]:
    """``build_report`` proper; the wrapper only opens the timings context."""
    with _timed("layout"):
        texts = fx.page_texts(bundle)
        layout = fx.page_layout(bundle)
    pname = contract_parser_name(bundle.get("parser_name") or (intake or {}).get("parser"))
    pver = parser_version(pname, bundle.get("jdf"))
    with _timed("quality"):
        pages, signatures = score_pages(bundle, texts, intake, layout=layout)
    page_quality = [p["quality_score"] for p in pages]
    visual_pages = [p.get("visual") for p in pages]
    documents = segment_pages(texts)
    mixed = len(documents) > 1
    notes: list[str] = []
    fields: list[dict] = []
    rules: list[dict] = []
    execution: dict[str, Any] = {}
    # Raw facts before any type decision (plan V4 Part 1, 2026-09-28): the
    # pool is built once from every readable source the bundle carries and each
    # segment projects its slice onto whatever schema it settles on.
    raw_pool, raw_stats = _raw.build_pool(texts, layout, tables=_tables.current_tables(), forms=bundle.get("forms"), documents=documents)
    execution["raw_candidates"] = raw_stats
    for seg in documents:
        seg_texts = segment_texts(texts, seg["pages"]) if mixed else texts
        # Classification by evidence before any extraction (module docstring):
        # the label pass is the cheap step, the LLM fill the expensive one, so
        # the type is settled first and the model is asked once, for one type.
        with _timed("classify"):
            settle_segment_type(seg, seg_texts, completion=completion, project_id=project_id, notes=notes)
        # (llm_fill_missing times its own model call under "llm_extract"; a
        # segment that needs no pass costs no measured time.)
        promote_with_grounded_pass(
            seg, seg_texts, layout=layout, parser_name=pname, parse_confidence=bundle.get("parse_confidence"),
            ocr_confidence=bundle.get("ocr_confidence"), page_quality=page_quality, visual_pages=visual_pages,
            completion=completion, project_id=project_id, notes=notes, execution=execution,
        )
        with _timed("extract"):
            seg_fields, seg_rules = extract_segment_fields(
                seg["document_type"], seg_texts, layout=layout, parser_name=pname, parse_confidence=bundle.get("parse_confidence"),
                ocr_confidence=bundle.get("ocr_confidence"), page_quality=page_quality, visual_pages=visual_pages,
                verification=verification, notes=notes, completion=completion, project_id=project_id,
                schema_mismatch=bool(seg.get("schema_mismatch")), execution=execution,
                candidates=_raw.candidates_for_pages(raw_pool, seg["pages"]),
            )
        for f in seg_fields:
            f["segment"] = seg["index"]
        for r in seg_rules:
            r["segment"] = seg["index"]
        seg["fields_total"] = len(seg_fields)
        seg["fields_found"] = fields_found_count(seg_fields)
        seg["pages_searched"] = list(seg["pages"])
        seg.pop("page_types", None)
        fields.extend(seg_fields)
        rules.extend(seg_rules)
    scope = _tables.current_scope()
    execution["page_quality"] = recalibrate_page_quality(pages, page_quality, fields, texts=texts, layout=layout,
                                                        discovered=scope.discovered if scope is not None else None, candidates=raw_pool)
    if mixed:
        classification = bundle_classification(documents)
        classification["family"] = fx.document_family("\n".join(texts))
        classification["validation"] = validate_classification("mixed_bundle", texts, family=classification["family"])
        classification["schema_mismatch"] = any(bool(seg.get("schema_mismatch")) for seg in documents)
        classification["suggestion"] = None
        classification["uncertainty"] = None
    else:
        classification = {k: documents[0][k] for k in ("document_type", "confidence", "basis", "matched_keywords")}
        for key in ("method", "detected", "evidence"):
            if key in documents[0]:
                classification[key] = documents[0][key]
        for key in ("family", "validation", "schema_mismatch", "suggestion", "uncertainty"):
            classification[key] = documents[0].get(key)
    classification["override"] = None
    scored = [s for s in page_quality if s is not None]
    doc_quality = round(sum(scored) / len(scored), 3) if scored else None
    text_chars = sum(len((t or "").strip()) for t in texts)
    form_template = form_template_flag(texts, fields)
    if form_template:
        notes.append(form_template["sentence"])
    quality_flags = sorted(
        {flag for p in pages for flag in p.get("flags") or []}
        | ({"mixed_bundle"} if mixed else set())
        | ({"no_text"} if text_chars < NO_TEXT_MIN_CHARS else set())
        | ({"form_template"} if form_template else set())
    )
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
    if mixed:
        # Spec §6: the upload is several documents; the router's single
        # material/modality no longer describes it. The router's own values
        # are kept underneath for the audit trail.
        material = {**material, "source_material_type": material.get("material_type"), "source_modality": material.get("modality"),
                    "material_type": "mixed_bundle", "modality": "mixed"}
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
        "documents": documents,
        "fields": fields,
        "extraction_notes": notes,
        "plausibility": rules,
        "conflicts": [],
        "verification": {
            "z3_status": (verification or {}).get("z3_status") if isinstance(verification, dict) else None,
            "redhat_status": (verification or {}).get("redhat_status") if isinstance(verification, dict) else None,
            "z3_violation_count": len(z3.get("violations") or []) if isinstance(z3, dict) else None,
            # ``run_verification_after_parse`` runs a >50-page document synchronously
            # while the async hand-off is deferred (docs/deferred.md); the report says
            # whether that branch was taken (plan V4 Part 3, 2026-09-28).
            "async_deferred": bool((verification or {}).get("async_deferred")) if isinstance(verification, dict) else False,
        },
        "review_summary": review_summary(fields, document_type=classification.get("document_type"),
                                         schema_mismatch=bool(classification.get("schema_mismatch"))),
        "quality_report": {
            "summary": None,
            "quality_sentence": quality_summary(pages, signature, numbers_flagged, documents=documents),
            "extraction_sentence": None,
            "text_chars": text_chars,
            "flags": quality_flags,
            "signature": signature,
            "numbers": {"flagged": numbers_flagged},
            "low_quality_pages": [p["page"] for p in pages if p.get("quality_score") is not None and float(p["quality_score"]) < fx.LOW_QUALITY_PAGE],
            "low_quality_threshold": fx.LOW_QUALITY_PAGE,
        },
        "page_coverage": page_coverage(documents, fields, texts, page_quality),
        "laya": (intake or {}).get("laya"),
        "replay": replay_state(fields, pname, pver, document_type=classification.get("document_type")),
        "raw_candidates": raw_pool,
        "projection": execution.get("projection"),
        "created_at": _now(),
        "_page_texts": texts,
        "_page_quality": page_quality,
        "_layout": layout,
    }
    with _timed("quality"):
        compose_quality_summary(report)
    # The saved Assure tree (import-pdf path) names the paragraph each value
    # came from; a Sources-pane upload has no tree and addresses the chunk only.
    report["tree_nodes_addressed"] = attach_tree_node_ids(report["fields"], tree)
    report["graph_integrity"] = graph_integrity(report["fields"])
    report["node_id_policy"] = fx.NODE_ID_POLICY
    report["identity"] = {"policy": fx.NODE_ID_POLICY, "derivation": fx.NODE_ID_DERIVATION}
    report["latency_class"] = latency_class(report["material_type"], report["modality"], classification.get("document_type"), len(documents))
    # ``extract`` is measured around the whole extraction call, which includes
    # the model pass; take the model's own time back out so the stages add up.
    timings["extract"] = round(max(0.0, timings.get("extract", 0.0) - timings.get("llm_extract", 0.0)), 3)
    timings["total"] = round((perf_counter() - t_start) * 1000.0, 3)
    report["timings_ms"] = {stage: float(timings.get(stage, 0.0)) for stage in TIMING_STAGES}
    # The run's own ledger (plan Part 2 / 7.1, 2026-09-27): what ran, what did
    # not, and why — read by the UI's execution panel and the proof run.
    report["execution"] = build_execution(report, execution, intake=intake, verification=verification)
    llm = report["execution"].get("llm_grounding") or {}
    if llm.get("fields_filled"):
        found_after = fields_found_count(fields)
        record_pipeline_pass(report, trigger="pipeline:llm_grounded", fields_changed=list(llm["fields_filled"]),
                             before_found=found_after - len(llm["fields_filled"]), after_found=found_after,
                             basis=f"grounded model pass ({llm.get('model')}) filled what the label pass left empty")
        report["execution"]["rerun"] = rerun_summary(report)
    return report


def recalibrate_page_quality(pages: list[dict], page_quality: list[float | None], fields: list[dict], *, texts: list[str], layout: list | None,
                             discovered: list[dict] | None = None, candidates: list[dict] | None = None) -> dict[str, Any]:
    """Plan V4 Part 4 (2026-09-28): the extraction's own readability evidence
    joins the page score. ``raw_candidates.grounding_success_by_page`` counts,
    per page, the located reads that had a usable value shape against the
    suspects; ``quality_probe.grounding_floor`` turns that share and the page's
    OCR word-confidence mean into a floor the probe score cannot fall under.
    Measured on the customer's site-report photo: probe 0.07 (low_res ×
    low_contrast × ocr 0.80) while six of the page's labels were read verbatim
    — lifted to 0.80; a debris scan whose reads are suspects keeps its score.
    Pages the floor lifted are re-judged: ``low_quality_page`` flags, the
    readability of absences and the decision policy, so a field's state and
    the page score never disagree. Records ``quality_score_probe`` beside the
    lifted score. Returns the ``execution.page_quality`` block."""
    qp = _quality_probe()
    success = _raw.grounding_success_by_page(fields, discovered=discovered, candidates=candidates, page_count=len(pages))
    lifted: list[int] = []
    for i, page in enumerate(pages):
        g = success[i] if i < len(success) else None
        page["grounding_success"] = g
        if g is None or qp is None or not hasattr(qp, "grounding_floor"):
            continue
        floor, basis = qp.grounding_floor(grounding_success=g, ocr_confidence=page.get("ocr_confidence"))
        if floor is None:
            continue
        old = page.get("quality_score")
        if old is None or floor > float(old):
            page["quality_score_probe"] = old
            page["quality_score"] = floor
            page["basis"] = f"{page.get('basis')}; lifted to {basis}"
            if i < len(page_quality):
                page_quality[i] = floor
            lifted.append(i + 1)
        else:
            page["basis"] = f"{page.get('basis')}; {basis} (below the probe score)"
    if lifted:
        layout_list = layout if isinstance(layout, list) else []
        for f in fields:
            if not isinstance(f, dict) or f.get("field_type") == "signature" or f.get("evidence_state") == "schema_mismatch":
                continue
            if f.get("value") is None and f.get("evidence_state") in ("not_on_document", "unreadable"):
                if f.get("reason") in fx.EVIDENCE_REASONS.values():
                    f["reason"] = None
                fx.attach_absent_evidence(f, texts, layout_list, page_quality)
                fx.apply_decision_policy(f)
            elif f.get("value") is not None and f.get("low_quality_page"):
                f["low_quality_page"] = False
                if str(f.get("reason") or "").startswith("page quality"):
                    f["reason"] = None
                fx.mark_low_quality_page(f, page_quality)
                fx.apply_decision_policy(f)
    return {"status": "completed", "pages_lifted": lifted, "grounding_success": success,
            "reason": None if any(s is not None for s in success) else "no located read on any page — probe scores stand",
            "rule": "floor = grounded-read share × OCR word-confidence mean; both required; never lowers a score"}


#: ``redhat_draft`` left this tuple 2026-09-28 (plan V4 Part 3): it mirrored
#: ``verification.redhat_status`` — the draft critique of the Assure side, not a
#: step of this pipeline — and read as a step that "did not run". The fact stays
#: on ``report["verification"]["redhat_status"]``.
EXECUTION_STEPS = ("laya", "z3", "redhat_graph", "llm_grounding", "rerun", "tables", "vision", "raw_candidates", "projection", "page_quality")


def rerun_summary(report: dict[str, Any]) -> dict[str, Any]:
    replay = report.get("replay") if isinstance(report.get("replay"), dict) else {}
    history = [h for h in (replay.get("history") or []) if isinstance(h, dict)]
    passes = [h for h in history if str(h.get("trigger") or "").startswith("pipeline:")]
    return {"passes": len(passes), "attempts": int(replay.get("attempts") or 0), "improved": any(h.get("improved") for h in passes),
            "fields_changed": sorted({n for h in passes for n in (h.get("fields_changed") or [])}), "stop_rule": replay.get("stop_rule")}


def build_execution(report: dict[str, Any], collected: dict[str, Any], *, intake: dict | None, verification: dict | None) -> dict[str, Any]:
    """``report["execution"]``: one entry per pipeline step with a status word
    and the counts that prove it, ``not_run`` with a reason when a step did
    not happen. Customer plan 2026-09-27 ("the system is not lying — it is
    not executing"): a report in which LAYA, Z3, the critique and the model
    pass leave no trace reads as if they never ran. Nothing here is computed
    from a default: LAYA from the router's block, Z3 from the verification
    summary, the model pass from ``llm_fill_missing``'s own counts."""
    laya = report.get("laya") if isinstance(report.get("laya"), dict) else None
    if laya:
        laya_block = {"status": "completed", "policy": laya.get("model") or laya.get("policy_version"), "escalate": laya.get("escalate"),
                      "human_review": laya.get("human_review"), "flagged_pages": laya.get("flagged_pages"), "probed_pages": laya.get("probed_pages"),
                      "suggested_route": laya.get("suggested_route"), "reasons": list(laya.get("reasons") or [])}
    else:
        laya_block = {"status": "not_run", "reason": "no intake triage block on this path (the router did not run: text or direct call)"}
    ver = report.get("verification") if isinstance(report.get("verification"), dict) else {}
    z3_status = ver.get("z3_status")
    z3_block = {"status": str(z3_status) if z3_status else "not_run", "violations": ver.get("z3_violation_count"),
                "reason": None if z3_status else "no verification summary was passed to the report",
                "async_deferred": bool(ver.get("async_deferred"))}
    llm = collected.get("llm_grounding") or {"status": "not_run", "model": None, "fields_offered": 0, "fields_grounded": 0, "fields_filled": [],
                                             "candidates_rejected": 0, "ms": 0.0, "reason": "no extraction segment offered fields"}
    out = {
        "laya": laya_block,
        "z3": z3_block,
        "redhat_graph": {"status": "not_run", "reason": "critique runs after the report is built (run_after_parse)"},
        "llm_grounding": llm,
        "rerun": rerun_summary(report),
        "tables": collected.get("tables") or {"status": "not_run", "reason": "no table pass on this run"},
        "vision": collected.get("vision") or {"status": "not_run", "reason": "vision runs after the report is built (run_after_parse)"},
        "ran_at": _now(),
    }
    for key, value in collected.items():
        if key not in out and isinstance(value, dict):
            out[key] = value
    return out


def load_tree_for_report(report: dict[str, Any]) -> dict | None:
    """The saved Assure tree the report's fields were addressed against
    (``revision_version`` of the project), or None for a Sources-pane upload.

    Replay determinism (2026-09-27, live compose stack): the worker run
    addressed the signature to its tree paragraph and element (``p-…``,
    ``p1e3:a2e2…``), the replay re-extracted without the tree and produced the
    chunk-level id — the proof read "signature changed" for the same bytes.
    Re-extraction now re-attaches the same tree."""
    version = report.get("revision_version")
    project_id = report.get("project_id")
    if not project_id or version in (None, ""):
        return None
    try:
        try:
            from ..db import jdf_repository as jdf_repo
        except ImportError:
            from db import jdf_repository as jdf_repo  # type: ignore
        tree = jdf_repo.fetch_jdf_at_version(str(project_id), int(version))
    except Exception:
        log.exception("parsure: could not load tree v%s for %s", version, project_id)
        return None
    return tree if isinstance(tree, dict) else None


def stamp_field_uids(report: dict[str, Any]) -> None:
    """``field_uid`` = ``field-<document_id>-<name>[-<segment>]`` on every field
    (plan Part 2.7 invariant 2, 2026-09-27): a pointer that survives reruns,
    revisions and exports even when the value moves to another node. Set once
    the report has a ``document_id``; a report without one keeps None."""
    doc = report.get("document_id")
    if not doc:
        return
    for f in report.get("fields") or []:
        if isinstance(f, dict) and f.get("name"):
            seg = f.get("segment")
            f["field_uid"] = f"field-{doc}-{f['name']}" + (f"-{seg}" if seg not in (None, 0) else "")


def refresh_report(report: dict[str, Any]) -> dict[str, Any]:
    """Recompute the derived blocks after a field changed (correct/dispute/override)."""
    fields = report.get("fields") or []
    stamp_field_uids(report)
    texts = report.get("_page_texts")
    if isinstance(texts, list) and isinstance(report.get("documents"), list):
        report["page_coverage"] = page_coverage(report["documents"], fields, texts, list(report.get("_page_quality") or []))
    classification = report.get("classification") if isinstance(report.get("classification"), dict) else {}
    document_type = classification.get("document_type")
    report["review_summary"] = review_summary(fields, document_type=document_type, schema_mismatch=bool(classification.get("schema_mismatch")))
    report["replay"] = replay_state(fields, str(report.get("parser_name") or ""), str(report.get("parser_version") or ""), report.get("replay"),
                                    document_type=document_type)
    if isinstance(report.get("quality_report"), dict):
        compose_quality_summary(report)
    report["graph_integrity"] = graph_integrity(fields)
    return report


def reextract_for_type(report: dict[str, Any], document_type: str, *, verification: dict | None = None, completion: Any = None, llm: bool = False,
                       by_evidence: bool = False) -> dict[str, Any]:
    """Classification override: re-run the label pass and the policy over the
    stored page texts. The reviewer named one type for the whole upload, so a
    mixed bundle collapses to a single segment of that type — the override is
    the reviewer's boundary decision.

    The grounded LLM pass is off here by default: this runs inside the
    override request on the web tier, and a model call (2–6 s on the local
    model, up to the 90 s bound on a stalled provider) is exactly the long
    work the web tier must not do. The report says so in
    ``extraction_notes``; a worker-side caller passes ``llm=True``.

    ``by_evidence=True`` applies ``reclassify_by_evidence`` to the requested
    type first (a caller that is not sure — a replay, a batch re-read — rather
    than a reviewer who is); when the evidence names another type the report's
    ``classification`` follows it and says why."""
    texts = list(report.get("_page_texts") or [])
    layout = report.get("_layout")
    notes: list[str] = []
    basis = "reviewer override"
    classification = report.setdefault("classification", {})
    # Remap before re-read (plan V4 Part 1.4): the stored pool is projected
    # onto the new schema first; the deterministic sources are rebuilt from the
    # stored texts and only *appended* (a report saved before the pool existed
    # gets one; nothing stored is rewritten or dropped).
    fresh_pool, raw_stats = _raw.build_pool(texts, layout if isinstance(layout, list) else None, tables=report.get("tables") or [],
                                             forms=None, vision=report.get("vision"), documents=None)
    raw_pool, added = _raw.merge_pool(report.get("raw_candidates"), fresh_pool)
    report["raw_candidates"] = raw_pool
    raw_stats.update(candidates=len(raw_pool), added=added, remap=True)
    if by_evidence:
        evidence = reclassify_by_evidence(document_type, None, texts)
        if evidence is not None:
            evidence["detected"] = {"document_type": document_type, "confidence": None, "basis": "requested type", "matched_keywords": []}
            document_type = evidence["document_type"]
            basis = evidence["basis"]
            classification.update({k: evidence[k] for k in ("document_type", "confidence", "basis", "method", "detected")})
    # Validation (2026-09-26): the requested type against the page's family.
    # A reviewer's choice stands, but when it disagrees with the cues *and*
    # the type's own fields are not on the page the fields are marked
    # ``schema_mismatch`` and the record says "Wrong document type".
    validation = validate_classification(document_type, texts)
    mismatch = bool(not validation["agrees"] and validation["type_specific"] < FAMILY_FALLBACK_MIN_TYPE_SPECIFIC)
    classification["validation"] = validation
    classification["schema_mismatch"] = mismatch
    classification["suggestion"] = family_suggestion(validation["family"], texts) if mismatch else None
    prior_grounded = {f["name"]: f for f in (report.get("fields") or [])
                      if isinstance(f, dict) and f.get("extraction_method") == "llm_grounded" and f.get("grounding_quote") and f.get("value") is not None}
    # Same tables as the first run (``report["tables"]``), so a re-read for
    # another type can fill its table-borne fields too (2026-09-27).
    execution_remap: dict[str, Any] = {}
    with _tables.extraction_scope(report.get("tables") or []) as scope:
        fields, rules = extract_segment_fields(
            document_type, texts, layout=layout if isinstance(layout, list) else None,
            parser_name=report.get("parser_name"), parse_confidence=None,
            ocr_confidence=None, page_quality=list(report.get("_page_quality") or []),
            visual_pages=[p.get("visual") for p in report.get("pages") or []],
            verification=verification, notes=notes, completion=completion, project_id=report.get("project_id"), llm=llm,
            schema_mismatch=mismatch, execution=execution_remap, candidates=raw_pool,
        )
    _tables.annotate_report(report, scope, replace=True)
    exe = report.setdefault("execution", {})
    exe["raw_candidates"] = raw_stats
    exe["projection"] = execution_remap.get("projection") or {"status": "not_run", "document_type": document_type, "reason": "no projection ran"}
    report["projection"] = exe["projection"]
    if isinstance(report.get("pages"), list) and report.get("_page_quality") is not None:
        pq = list(report.get("_page_quality") or [])
        exe["page_quality"] = recalibrate_page_quality(report["pages"], pq, fields, texts=texts, layout=layout if isinstance(layout, list) else None,
                                                     discovered=list(scope.discovered), candidates=raw_pool)
        report["_page_quality"] = pq
    if prior_grounded and not mismatch:
        fields = carry_grounded_values(document_type, fields, prior_grounded, texts=texts, layout=layout if isinstance(layout, list) else None,
                                       report=report, verification=verification, notes=notes)
    for f in fields:
        f["segment"] = 0
    for r in rules:
        r["segment"] = 0
    report["fields"] = fields
    report["plausibility"] = rules
    report["extraction_notes"] = notes
    report["documents"] = [{"index": 0, "pages": list(range(1, len(texts) + 1)), "pages_searched": list(range(1, len(texts) + 1)),
                            "document_type": document_type,
                            "confidence": classification.get("confidence") if by_evidence else None, "basis": basis,
                            # A reviewer's override has no keyword evidence of its own; only the
                            # evidence path carries the hits it was decided on.
                            "matched_keywords": list(classification.get("matched_keywords") or []) if by_evidence else [],
                            "fields_total": len(fields), "fields_found": fields_found_count(fields)}]
    # Same tree as the first run, so element/tree ids match and a replay of
    # the same bytes compares equal (see load_tree_for_report).
    report["tree_nodes_addressed"] = attach_tree_node_ids(fields, load_tree_for_report(report))
    return refresh_report(report)


def assign_document_id(report: dict[str, Any], *, result: dict | None, file_bytes: bytes | None) -> str:
    """A ``document_id`` the report never lacks, and where it came from.

    ``document_id_source``: ``ingest`` — the pipeline's id (a saved revision or
    a Sources vault row); ``content_hash`` — ``doc-`` + SHA-256 of the bytes,
    first 16 hex, so a re-upload of the same file is the same document across
    reports and exports (``list_reports`` dedupes by it); ``generated`` — a
    random id, only when neither is available (a caller that passed no bytes).
    A random id is still an id: an export row never carries an empty one."""
    given = (result or {}).get("document_id") if isinstance(result, dict) else None
    if given:
        report["document_id"] = str(given)
        report["document_id_source"] = "ingest"
    elif file_bytes:
        report["document_id"] = "doc-" + hashlib.sha256(file_bytes).hexdigest()[:16]
        report["document_id_source"] = "content_hash"
    else:
        report["document_id"] = "doc-" + uuid.uuid4().hex[:16]
        report["document_id_source"] = "generated"
    return report["document_id"]


def redhat_graph_summary(report: dict[str, Any], critique: dict | list | None) -> dict[str, Any]:
    block = report.get("redhat") if isinstance(report.get("redhat"), dict) else {}
    counts = block.get("counts") if isinstance(block.get("counts"), dict) else {}
    notes = list((critique or {}).get("notes") or []) if isinstance(critique, dict) else list(block.get("notes") or [])
    if any(n.startswith("unsupported-claim check ran") for n in notes):
        model_check = "ran"
    elif any("unsupported-claim check skipped" in n or "unsupported-claim check did not run" in n for n in notes):
        model_check = "skipped"
    else:
        model_check = "not_recorded"
    return {"status": "completed", "policy": block.get("policy") or "rh-graph-v1", "findings": len(block.get("findings") or []),
            "high": int(counts.get("high") or 0), "medium": int(counts.get("medium") or 0), "low": int(counts.get("low") or 0),
            "model_check": model_check, "notes": notes[:6]}


#: Findings whose anchored field is worth a second, hinted read: the rule read
#: the value as debris/unsupported, or the field holds a suspect.
TARGETED_RULES = ("suspect_value", "unsupported_claim")


def redhat_targeted_pass(report: dict[str, Any], *, tree: dict | None, completion: Any, llm: bool, rg: Any, model_check: bool = False) -> dict[str, Any] | None:
    """One hinted grounded pass over the fields the critique points at.

    Candidates: fields named by a finding's anchor that have no usable value
    (``found_suspect`` — debris under the label) or whose finding's rule is
    in ``TARGETED_RULES``. Each gets the finding's rationale as the prompt
    hint. A grounded answer replaces the field, the policy re-runs on it, the
    tree ids are re-attached, the critique runs again over the changed graph
    (``redhat.previous_counts`` keeps the first run's counts) and
    ``replay.history`` gets a ``pipeline:redhat_targeted`` entry. Bounded to
    one pass per report; nothing happens when the model path is off (the
    ledger says so). Returns the ledger entry or None."""
    exe = report.setdefault("execution", {})
    block = report.get("redhat") if isinstance(report.get("redhat"), dict) else {}
    fields = report.get("fields") or []
    by_name = {f.get("name"): f for f in fields if isinstance(f, dict)}
    hints: dict[str, str] = {}
    for finding in block.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        name = (finding.get("anchor") or {}).get("field") if isinstance(finding.get("anchor"), dict) else None
        f = by_name.get(name)
        if not f or f.get("field_type") == "signature":
            continue
        rule = str(finding.get("rule") or "")
        if f.get("evidence_state") == "found_suspect" or any(rule.startswith(r) for r in TARGETED_RULES):
            hints.setdefault(name, f"{finding.get('title')}: {finding.get('rationale')}"[:300])
    if not hints:
        exe["redhat_targeted"] = {"status": "not_needed", "fields": [], "reason": "no finding names a field worth a second read"}
        return None
    if not llm and lx.is_app_completion(completion):
        exe["redhat_targeted"] = {"status": "disabled", "fields": sorted(hints), "reason": "PARSURE_LLM_EXTRACTION is off"}
        return None
    texts = list(report.get("_page_texts") or [])
    layout = report.get("_layout") if isinstance(report.get("_layout"), list) else None
    doc_type = str((report.get("classification") or {}).get("document_type") or "")
    notes: list[str] = []
    stats: dict[str, Any] = {}
    before = field_facts(fields)
    before_found = fields_found_count(fields)
    # Suspects hold ``value None`` already; the pass offers exactly the hinted names.
    new_fields = llm_fill_missing(
        doc_type, texts, fields, completion=completion, notes=notes, parser_name=report.get("parser_name"), parse_confidence=None,
        ocr_confidence=None, page_quality=list(report.get("_page_quality") or []), visual_pages=[p.get("visual") for p in report.get("pages") or []],
        layout=layout, project_id=report.get("project_id"), execution=stats, hints=hints, only=sorted(hints),
    )
    changed = [n for n, f in ((f.get("name"), f) for f in new_fields) if n in hints and (f.get("value"), f.get("element_id")) != (before.get(n, (None, None, None))[0], before.get(n, (None, None, None))[1])]
    exe["redhat_targeted"] = {**(stats.get("llm_grounding") or {}), "fields": sorted(hints), "changed": sorted(changed), "hints": hints}
    if not changed:
        report["extraction_notes"] = list(report.get("extraction_notes") or []) + notes
        return None
    seg_of = {f.get("name"): f.get("segment") for f in fields if isinstance(f, dict)}
    for f in new_fields:
        if f.get("name") in changed:
            f["segment"] = seg_of.get(f.get("name"), 0)
            # The document-level Z3 summary is on the report; give the policy the same facts the first run had.
            status = (report.get("verification") or {}).get("z3_status")
            if f.get("value") is not None and not f.get("verification_source") and status in ("PASS", "VIOLATION"):
                # Z3 ran over the document before this value existed; the
                # document-level figure is carried, and the basis says the new
                # value itself was not re-verified (plan V4 Part 3, 2026-09-28).
                f["verification_confidence"] = fx.DEFAULT_VERIFICATION_CONFIDENCE
                f["verification_basis"] = (f"not re-verified after grounded change: document-level Z3 {status} predates this value; "
                                           f"no violation attached to this field (V1 default {fx.DEFAULT_VERIFICATION_CONFIDENCE})")
            fx.mark_low_quality_page(f, list(report.get("_page_quality") or []))
            fx.apply_decision_policy(f)
    report["fields"] = new_fields
    report["extraction_notes"] = list(report.get("extraction_notes") or []) + notes
    report["tree_nodes_addressed"] = attach_tree_node_ids(report["fields"], tree if tree is not None else load_tree_for_report(report))
    refresh_report(report)
    previous_counts = dict(block.get("counts") or {})
    critique = rg.critique_report(report, tree=tree, completion=None, llm=model_check)
    rg.attach_findings(report, critique)
    report["redhat"]["previous_counts"] = previous_counts
    report["redhat"]["targeted_pass"] = {"fields": sorted(hints), "changed": sorted(changed)}
    exe["redhat_graph"] = redhat_graph_summary(report, critique)
    entry = record_pipeline_pass(report, trigger="pipeline:redhat_targeted", fields_changed=changed, before_found=before_found,
                                 after_found=fields_found_count(report["fields"]),
                                 basis=f"critique named {len(hints)} field(s); the hinted grounded pass replaced {len(changed)}")
    exe["rerun"] = rerun_summary(report)
    return entry


def carry_grounded_values(document_type: str, fields: list[dict], prior: dict[str, dict], *, texts: list[str], layout: list | None,
                          report: dict[str, Any], verification: dict | None, notes: list[str]) -> list[dict]:
    """Re-verify, without a model, the values the first run grounded with the
    model: a prior ``llm_grounded`` field whose ``grounding_quote`` is still
    verbatim on the page with the value inside it is rebuilt at that span
    (same contract record, same element id); one that no longer re-grounds
    is left as the label pass found it. Measured live 2026-09-27: the replay
    (label pass only) dropped ``insured_name`` that the worker's grounded pass
    had found in the footer, and the determinism proof read "changed" for the
    same bytes — the proof was comparing two different pipelines."""
    specs = {s.name: s for s in fx.FIELD_TAXONOMY.get(document_type) or []}
    carried: list[str] = []
    out = []
    for f in fields:
        old = prior.get(f.get("name"))
        spec = specs.get(f.get("name"))
        if not old or not spec or f.get("value") is not None:
            out.append(f)
            continue
        grounded = lx.ground_candidate(spec, texts, old.get("grounding_quote"), old.get("raw") if old.get("raw") is not None else old.get("value"))
        if grounded is None:
            out.append(f)
            continue
        page_index, raw, start, end = grounded
        new = fx.build_found_field(
            spec, page_index=page_index, raw=raw, start=start, end=end, parser_name=report.get("parser_name"), parse_confidence=None,
            ocr_confidence=None, page_quality=list(report.get("_page_quality") or []), visual_pages=[p.get("visual") for p in report.get("pages") or []],
            layout=list(layout or []), method="llm_grounded", page_text=texts[page_index],
        )
        if new.get("value") is None:
            out.append(f)
            continue
        new["grounding_quote"] = old.get("grounding_quote")
        new["grounding_model"] = old.get("grounding_model")
        new["grounding_source"] = old.get("grounding_source") or "llm"
        new["grounding"] = old.get("grounding")
        new["carried_from"] = {"pass": "llm_grounded", "reverified": "quote found verbatim on the page, value inside it"}
        fx.mark_low_quality_page(new, list(report.get("_page_quality") or []))
        out.append(new)
        carried.append(str(f.get("name")))
    if carried:
        decide_fields(out, verification=verification, document_type=document_type)
        notes.append(f"carried {len(carried)} grounded value(s) re-verified verbatim without a model call: {', '.join(carried)}")
    return out


def attach_vision_candidates(report: dict[str, Any]) -> int:
    """Vision facts into the raw pool (plan V4 Part 1.1, ``image_vision``
    source): appended after ``services/vision`` ran, corroborated against the
    stored page texts, never rewriting what the build already pooled. Returns
    the number appended; a failure is a ledger note, not a lost report."""
    exe = report.setdefault("execution", {})
    stats = exe.get("raw_candidates") if isinstance(exe.get("raw_candidates"), dict) else {}
    try:
        texts = list(report.get("_page_texts") or [])
        layout = report.get("_layout") if isinstance(report.get("_layout"), list) else None
        new, _counts = _raw.merge_new(list(_raw.vision_candidates(report.get("vision"), texts, layout)), documents=report.get("documents"))
        pool, added = _raw.merge_pool(report.get("raw_candidates"), new)
        report["raw_candidates"] = pool
        stats.update(candidates=len(pool), vision_added=added,
                     corroborated_vision=sum(1 for c in pool if c.get("source_kind") == "image_vision" and c.get("corroborated")),
                     uncorroborated_vision=sum(1 for c in pool if c.get("source_kind") == "image_vision" and not c.get("corroborated")))
        exe["raw_candidates"] = stats
        return added
    except Exception as exc:  # noqa: BLE001 — additive layer
        log.exception("parsure: vision candidates failed for %s", report.get("filename"))
        stats["vision_error"] = f"{type(exc).__name__}: {exc}"[:200]
        exe["raw_candidates"] = stats
        return 0


def attach_conflicts(project_id: str, report: dict[str, Any]) -> dict[str, Any]:
    """Cross-document conflicts over the project's saved reports plus this one."""
    try:
        from ..db import parsure_repository as repo
    except ImportError:
        from db import parsure_repository as repo  # type: ignore
    others = [r for r in repo.list_reports(project_id) if r.get("report_id") != report.get("report_id")]
    # The projection's own conflicts (a raw candidate disagreeing with the label
    # pass, plan V4 Part 1.2) stay beside the cross-document ones.
    projection = report.get("projection") if isinstance(report.get("projection"), dict) else {}
    own = [c for c in (projection.get("conflicts") or []) if isinstance(c, dict)]
    report["conflicts"] = own + fx.cross_document_conflicts(others + [report])
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
    completion: Any = None,
    tree: dict | None = None,
) -> dict[str, Any] | None:
    """Build, save and log the intake report. Returns ``{"report_id", "report"}`` or None on error.

    ``file_bytes`` is not probed here (the visual probe already ran in the
    intake router and arrives in ``intake["visual_pages"]``; probing again
    would be the duplicate router spec §8 forbids). It has one use: when the
    pipeline gave no ``document_id`` (handoff 2026-09-27, "document_id Is
    Null" — the Sources-panel path and direct callers), the id is derived from
    the bytes so the same file gets the same id on every upload and export.
    """
    try:
        from ..db import parsure_repository as repo
    except ImportError:
        from db import parsure_repository as repo  # type: ignore
    try:
        report = build_report(
            project_id, bundle=bundle, verification=verification, filename=filename, result=result, job_id=job_id, intake=intake,
            completion=completion, tree=tree,
        )
        assign_document_id(report, result=result, file_bytes=file_bytes)
        attach_conflicts(project_id, report)
        # Automated Red-Hat over the intake evidence graph (plan P0, 2026-09-26):
        # rules first, a grounded model check second, never a manual label.
        # Runs on every report; a failure is a note, not a missing pass.
        try:
            try:
                from ..services import redhat_graph as rg
            except ImportError:
                from services import redhat_graph as rg  # type: ignore
            try:
                from ..services.llm_extraction import llm_extraction_enabled as _llm_on
            except ImportError:
                from services.llm_extraction import llm_extraction_enabled as _llm_on  # type: ignore
            # PARSURE_LLM_EXTRACTION=0 means "no model calls on intake" for the
            # critique's grounded check too; the rules always run.
            # The critique's model check uses the app's own model path (not the
            # extraction's injected completion): the extraction call budget and
            # its tests stay one call per document.
            # Both gates: PARSURE_LLM_EXTRACTION (no model calls on intake) and
            # PARSURE_REDHAT_LLM (the critique's own switch) — the proof test
            # runs with the first on and the second off and must not reach a model.
            model_check = _llm_on() and rg.llm_enabled()
            critique = rg.critique_report(report, tree=tree, completion=None, llm=model_check)
            rg.attach_findings(report, critique)
            report.setdefault("execution", {})["redhat_graph"] = redhat_graph_summary(report, critique)
            # Red-Hat-targeted second look (plan Part 2.4 / 2.8, 2026-09-27):
            # the fields the critique names as suspect or unsupported are
            # offered once more to the grounded model pass with the finding as
            # the hint; a grounded answer replaces the debris, the critique
            # runs again over the changed graph, and the ledger records both.
            redhat_targeted_pass(report, tree=tree, completion=completion, llm=_llm_on(), rg=rg, model_check=model_check)
        except Exception as exc:
            log.exception("parsure: red-hat graph critique failed for %s", filename)
            report.setdefault("redhat", {"policy": "rh-graph-v1", "findings": [], "counts": {}, "classes": {},
                                         "notes": [f"critique failed: {exc.__class__.__name__}"]})
            report.setdefault("execution", {})["redhat_graph"] = {"status": "failed", "policy": "rh-graph-v1", "findings": 0, "high": 0,
                                                                  "reason": f"{exc.__class__.__name__}: {exc}"[:200]}
        report.setdefault("execution", {})["rerun"] = rerun_summary(report)
        stamp_field_uids(report)
        # Pictures (plan Part 5, 2026-09-27): services/vision decides whether a
        # page is a photo worth a multimodal read; disabled or absent, it says
        # so in report["vision"] / execution.vision. Never a failed ingest.
        try:
            try:
                from ..services import vision as _vision
            except ImportError:
                from services import vision as _vision  # type: ignore
            _vision.attach_vision(report, file_bytes=file_bytes, intake=intake)
        except ImportError:
            report.setdefault("execution", {})["vision"] = {"status": "not_run", "reason": "vision module not present"}
        except Exception as exc:  # noqa: BLE001 — advisory
            log.exception("parsure: vision pass failed for %s", filename)
            report.setdefault("execution", {})["vision"] = {"status": "failed", "reason": f"{exc.__class__.__name__}: {exc}"[:200]}
        attach_vision_candidates(report)
        report["execution"]["ran_at"] = _now()
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
            **{k: report["classification"].get(k) for k in ("document_type", "confidence", "basis", "method", "detected", "validation", "schema_mismatch", "suggestion")},
            "documents": [{k: d[k] for k in ("index", "pages", "document_type")} for d in report["documents"]],
        })
        repo.log_event(project_id, "fields_extracted", report_id=rid, payload={
            "fields_total": len(report["fields"]), "fields_found": fields_found_count(report["fields"]),
            "found": fields_found_count(report["fields"]),
            "llm_grounded": sum(1 for f in report["fields"] if f.get("extraction_method") == "llm_grounded"),
            "extraction_notes": report["extraction_notes"],
            "graph_integrity": report.get("graph_integrity"),
            "evidence_states": report["review_summary"].get("evidence_states"),
        })
        repo.log_event(project_id, "decision_applied", report_id=rid, payload={
            **{k: v for k, v in report["review_summary"].items() if k != "reasons"},
            "policy_version": POLICY_VERSION, "conflicts": len(report["conflicts"]),
        })
        return {"report_id": report_id, "report": repo.public_report(report)}
    except Exception:
        log.exception("parsure: run_after_parse failed for %s (project %s)", filename, project_id)
        return None
