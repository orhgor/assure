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

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

try:
    from ..services import field_extractor as fx
    from ..services import llm_extraction as lx
except ImportError:
    from services import field_extractor as fx  # type: ignore
    from services import llm_extraction as lx  # type: ignore

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
#: A family's schema is kept (family fallback) only when it finds this many
#: type-specific fields; below it the document is ``<family>_unknown``.
FAMILY_FALLBACK_MIN_TYPE_SPECIFIC = 2
#: The family's confidence ceiling when the family fallback names a type: the
#: found ratio, and never above the keyword cap.
FAMILY_FALLBACK_CAP = fx.CLASSIFICATION_CAP
FAMILY_WORDS = {"auto": "Auto", "property": "Property", "real_estate_transaction": "Real estate transaction", "medical": "Medical"}
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


def quality_summary(pages: list[dict], signature: dict | None, numbers_flagged: int, *, documents: list[dict] | None = None) -> str:
    """One calm sentence a reviewer can read at a glance."""
    total = len(pages)
    if not total:
        return "No pages were parsed."
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
        "evidence_states": states,
        "schema_mismatch": bool(schema_mismatch),
        "reasons": top,
    }


def replay_state(fields: list[dict], parser_name: str, parser_ver: str, previous: dict | None = None, *, document_type: Any = None) -> dict[str, Any]:
    """Spec §7: eligibility is recorded, execution is not (no durable replay path yet)."""
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
    return {
        "eligible": bool(reasons),
        "reasons": reasons,
        "replayed": False,
        "history": list((previous or {}).get("history") or []),
    }


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
) -> list[dict]:
    """Offer the label pass's empty fields to the grounded LLM pass and merge
    what it can prove. The signature field is never offered; a field the pass
    cannot ground stays exactly as it was. Never raises (the pass itself
    does not); ``notes`` collects its skip/reject lines."""
    specs = {s.name: s for s in fx.FIELD_TAXONOMY.get(document_type) or []}
    missing = [specs[f["name"]] for f in fields if f.get("value") is None and f["name"] in specs and specs[f["name"]].field_type != "signature"]
    if not missing:
        return fields
    try:
        filled = lx.extract_missing_fields(
            document_type, texts, missing, completion=completion, notes=notes, parser_name=parser_name,
            parse_confidence=parse_confidence, ocr_confidence=ocr_confidence, page_quality=page_quality,
            visual_pages=visual_pages, layout=layout, project_id=project_id,
        )
    except Exception as exc:  # noqa: BLE001 — advisory pass
        log.exception("llm_fill_missing failed")
        notes.append(f"llm extraction skipped: {type(exc).__name__}: {exc}")
        return fields
    by_name = {f["name"]: f for f in filled if f.get("value") is not None}
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
        "matched_keywords": [],
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
    if suggestion and suggestion["type_specific"] >= FAMILY_FALLBACK_MIN_TYPE_SPECIFIC:
        best = suggestion["document_type"]
        return {
            "document_type": best,
            "confidence": round(min(FAMILY_FALLBACK_CAP, suggestion["found"] / suggestion["total"]), 3),
            "basis": (f"family {fam} ({family['basis']}); {best} finds {suggestion['found']}/{suggestion['total']} fields, "
                      f"{suggestion['type_specific']} type-specific ({', '.join(suggestion['type_specific_fields'][:6])}) ({said})"),
            "matched_keywords": [],
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
                  f"no schema is forced ({said})"),
        "matched_keywords": [],
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
) -> tuple[list[dict], list[dict]]:
    """Label pass → grounded LLM fill (``llm=True``) → plausibility/Z3 → 3-rule
    policy, for one document (segment). ``llm=False`` leaves a note instead.
    ``schema_mismatch=True`` (the type's family disagrees with the page and
    its fields are not there) skips the model — no grounded value can make a
    wrong schema right — and marks every field ``schema_mismatch`` after the
    policy ran."""
    fields = fx.extract_fields(
        document_type, texts, layout=layout, parser_name=parser_name, parse_confidence=parse_confidence,
        ocr_confidence=ocr_confidence, page_quality=page_quality, visual_pages=visual_pages,
    )
    if schema_mismatch:
        if fields:
            notes.append(f"llm extraction skipped: schema mismatch — {document_type} fields are not applicable to this page")
    elif llm:
        fields = llm_fill_missing(
            document_type, texts, fields, completion=completion, notes=notes, parser_name=parser_name,
            parse_confidence=parse_confidence, ocr_confidence=ocr_confidence, page_quality=page_quality,
            visual_pages=visual_pages, layout=layout, project_id=project_id,
        )
    elif any(f.get("value") is None and f.get("field_type") != "signature" for f in fields):
        notes.append("llm extraction skipped: not run on this path (label pass only)")
    fields, rules = decide_fields(fields, verification=verification, document_type=document_type)
    if schema_mismatch:
        fx.mark_schema_mismatch(fields)
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
        if nid is None and evidence and evidence.get("kind") == "absent" and root:
            # An absent field with no layout anchor (flat text, no chunk ids)
            # hangs off the document's first node so the graph has no orphan;
            # the evidence says it is the root, not a paragraph it was read from.
            nid = root
            f["field_source_node_id"] = f["field_source_node_id"] or root
            evidence["anchor_node_id"] = evidence.get("anchor_node_id") or root
            evidence["anchor_kind"] = evidence.get("anchor_kind") or "document_root"
        f["tree_node_id"] = nid
        if nid:
            n += 1
            if isinstance(f.get("source_span"), dict):
                f["source_span"]["node_id"] = nid
    return n


def graph_integrity(fields: list[dict]) -> dict[str, Any]:
    """``{"fields", "anchored", "orphans", "absent_anchored", "checked_at",
    "basis"}`` — every field's link into the JDF graph, counted once the
    tree ids are attached. A field is anchored when ``field_source_node_id``
    (or ``tree_node_id``) is set; an absent field's anchor is the node it was
    searched from (``evidence.anchor_node_id``). ``orphans`` is the count with
    no node at all — zero whenever the parse produced any node id; a flat
    text upload with none says so in ``basis`` rather than inventing one."""
    total = len(fields)
    anchored = sum(1 for f in fields if f.get("field_source_node_id") or f.get("tree_node_id"))
    absent_anchored = sum(1 for f in fields if f.get("value") is None and (f.get("field_source_node_id") or f.get("tree_node_id")))
    orphans = total - anchored
    if not total:
        basis = "no fields"
    elif orphans == 0:
        basis = "every field names a JDF node (found: the node its value sits on; absent: the anchor it was searched from)"
    else:
        basis = f"{orphans} field{'s' if orphans != 1 else ''} without a node: the parse produced no node ids to anchor to"
    return {"fields": total, "anchored": anchored, "orphans": orphans, "absent_anchored": absent_anchored, "checked_at": _now(), "basis": basis}


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
    texts = fx.page_texts(bundle)
    layout = fx.page_layout(bundle)
    pname = contract_parser_name(bundle.get("parser_name") or (intake or {}).get("parser"))
    pver = parser_version(pname, bundle.get("jdf"))
    pages, signatures = score_pages(bundle, texts, intake)
    page_quality = [p["quality_score"] for p in pages]
    visual_pages = [p.get("visual") for p in pages]
    documents = segment_pages(texts)
    mixed = len(documents) > 1
    notes: list[str] = []
    fields: list[dict] = []
    rules: list[dict] = []
    for seg in documents:
        seg_texts = segment_texts(texts, seg["pages"]) if mixed else texts
        # Classification by evidence before any extraction (module docstring):
        # the label pass is the cheap step, the LLM fill the expensive one, so
        # the type is settled first and the model is asked once, for one type.
        settle_segment_type(seg, seg_texts, completion=completion, project_id=project_id, notes=notes)
        seg_fields, seg_rules = extract_segment_fields(
            seg["document_type"], seg_texts, layout=layout, parser_name=pname, parse_confidence=bundle.get("parse_confidence"),
            ocr_confidence=bundle.get("ocr_confidence"), page_quality=page_quality, visual_pages=visual_pages,
            verification=verification, notes=notes, completion=completion, project_id=project_id,
            schema_mismatch=bool(seg.get("schema_mismatch")),
        )
        for f in seg_fields:
            f["segment"] = seg["index"]
        for r in seg_rules:
            r["segment"] = seg["index"]
        seg["fields_total"] = len(seg_fields)
        seg["fields_found"] = fields_found_count(seg_fields)
        seg.pop("page_types", None)
        fields.extend(seg_fields)
        rules.extend(seg_rules)
    if mixed:
        classification = bundle_classification(documents)
        classification["family"] = fx.document_family("\n".join(texts))
        classification["validation"] = validate_classification("mixed_bundle", texts, family=classification["family"])
        classification["schema_mismatch"] = any(bool(seg.get("schema_mismatch")) for seg in documents)
        classification["suggestion"] = None
    else:
        classification = {k: documents[0][k] for k in ("document_type", "confidence", "basis", "matched_keywords")}
        for key in ("method", "detected", "evidence"):
            if key in documents[0]:
                classification[key] = documents[0][key]
        for key in ("family", "validation", "schema_mismatch", "suggestion"):
            classification[key] = documents[0].get(key)
    classification["override"] = None
    scored = [s for s in page_quality if s is not None]
    doc_quality = round(sum(scored) / len(scored), 3) if scored else None
    text_chars = sum(len((t or "").strip()) for t in texts)
    quality_flags = sorted(
        {flag for p in pages for flag in p.get("flags") or []}
        | ({"mixed_bundle"} if mixed else set())
        | ({"no_text"} if text_chars < NO_TEXT_MIN_CHARS else set())
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
        },
        "laya": (intake or {}).get("laya"),
        "replay": replay_state(fields, pname, pver, document_type=classification.get("document_type")),
        "created_at": _now(),
        "_page_texts": texts,
        "_page_quality": page_quality,
        "_layout": layout,
    }
    compose_quality_summary(report)
    # The saved Assure tree (import-pdf path) names the paragraph each value
    # came from; a Sources-pane upload has no tree and addresses the chunk only.
    report["tree_nodes_addressed"] = attach_tree_node_ids(report["fields"], tree)
    report["graph_integrity"] = graph_integrity(report["fields"])
    return report


def refresh_report(report: dict[str, Any]) -> dict[str, Any]:
    """Recompute the derived blocks after a field changed (correct/dispute/override)."""
    fields = report.get("fields") or []
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
    fields, rules = extract_segment_fields(
        document_type, texts, layout=layout if isinstance(layout, list) else None,
        parser_name=report.get("parser_name"), parse_confidence=None,
        ocr_confidence=None, page_quality=list(report.get("_page_quality") or []),
        visual_pages=[p.get("visual") for p in report.get("pages") or []],
        verification=verification, notes=notes, completion=completion, project_id=report.get("project_id"), llm=llm,
        schema_mismatch=mismatch,
    )
    for f in fields:
        f["segment"] = 0
    for r in rules:
        r["segment"] = 0
    report["fields"] = fields
    report["plausibility"] = rules
    report["extraction_notes"] = notes
    report["documents"] = [{"index": 0, "pages": list(range(1, len(texts) + 1)), "document_type": document_type,
                            "confidence": classification.get("confidence") if by_evidence else None, "basis": basis, "matched_keywords": [],
                            "fields_total": len(fields), "fields_found": fields_found_count(fields)}]
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
    completion: Any = None,
    tree: dict | None = None,
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
            completion=completion, tree=tree,
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
